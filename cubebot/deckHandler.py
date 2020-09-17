import re
import logging
import json
import binascii
import config
import scryfall
from datetime import datetime
import deckstat_interface as deckstat
from filters import restrict, UserType, DeckConv, GameStates
from model import session, Player, Deck, Card, CubeList, DeckList
from pn532 import PN532_SPI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CommandHandler, CallbackQueryHandler, ConversationHandler, MessageHandler, Filters
from telegram.ext.dispatcher import run_async
from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound

class DeckHandler:
    """ Chacun scan son deck l'un après l'autre
    Puis l'édition du deck peut se faire après
    """

    def __init__(self, dispatcher, game):
        self.game = game
        self.cubelist = session.query(CubeList).filter(CubeList.cube_id == game.cube.id,
                                                       CubeList.uid != None).all()
        self.current_user = None
        self.deck, self.scanned, self.user_scanned = None, [], []
        self.loop = False # Scan loop
        # Scanning device config
        self.pn532 = PN532_SPI(debug=False, reset=20, cs=4)
        self.pn532.SAM_configuration()
        
        # Handlers
        self.scan_handler = CommandHandler("deck", self.new_deck)
        dispatcher.add_handler(self.scan_handler)
        self.scan_buttons_handler = CallbackQueryHandler(self.scan_buttons)
        # Conversation Handler for deck title and description
        self.deck_conv_handler = self.deck_conv_handler()
        dispatcher.add_handler(self.deck_conv_handler)
    
    def get_scan_keyboard(self, count=0):
        if count:
            keyboard = [[InlineKeyboardButton("Corriger", callback_data='1'),
                        InlineKeyboardButton("Stats", callback_data='2'),
                        InlineKeyboardButton("Annuler", callback_data='0')],
                        [InlineKeyboardButton("Soumettre Deck", callback_data='3')]]
        else:
            keyboard = [[InlineKeyboardButton("Annuler", callback_data='0'),
                        InlineKeyboardButton("Soumettre", callback_data='3')]]
        return keyboard
        
    def get_deck_keyboard(self, stat=False):
        """Get Keyboard depending of game and dialog state
        stat avoid clicking multiple times on stat button causing a bug
        game state avoid modifying notes once the game is on
        """
        if self.game.state == GameStates.INIT.name :
            if not stat:
                keyboard = [[InlineKeyboardButton("Nom", callback_data=DeckConv.NAME.name),
                         InlineKeyboardButton("Description", callback_data=DeckConv.DESCR.name)],
                         [InlineKeyboardButton("Cartes", callback_data=DeckConv.CARDS.name),
                         InlineKeyboardButton("Notes", callback_data=DeckConv.NOTE.name),
                         InlineKeyboardButton("Stats", callback_data=DeckConv.STATS.name),
                         InlineKeyboardButton("Tokens", callback_data=DeckConv.TOKEN.name)],
                         [InlineKeyboardButton("Sortir", callback_data=DeckConv.CANCEL.name)]]
            else:
                keyboard = [[InlineKeyboardButton("Nom", callback_data=DeckConv.NAME.name),
                    InlineKeyboardButton("Description", callback_data=DeckConv.DESCR.name)],
                     [InlineKeyboardButton("Cartes", callback_data=DeckConv.CARDS.name),
                     InlineKeyboardButton("Notes", callback_data=DeckConv.NOTE.name),
                     InlineKeyboardButton("Tokens", callback_data=DeckConv.TOKEN.name)],
                     [InlineKeyboardButton("Sortir", callback_data=DeckConv.CANCEL.name)]]
        else:
            if not stat:
                keyboard = [[InlineKeyboardButton("Nom", callback_data=DeckConv.NAME.name),
                         InlineKeyboardButton("Description", callback_data=DeckConv.DESCR.name)],
                         [InlineKeyboardButton("Cartes", callback_data=DeckConv.CARDS.name),
                         InlineKeyboardButton("Stats", callback_data=DeckConv.STATS.name),
                         InlineKeyboardButton("Tokens", callback_data=DeckConv.TOKEN.name)],
                         [InlineKeyboardButton("Sortir", callback_data=DeckConv.CANCEL.name)]]
            else:
                keyboard = [[InlineKeyboardButton("Nom", callback_data=DeckConv.NAME.name),
                     InlineKeyboardButton("Description", callback_data=DeckConv.DESCR.name)],
                     [InlineKeyboardButton("Cartes", callback_data=DeckConv.CARDS.name),
                     InlineKeyboardButton("Tokens", callback_data=DeckConv.TOKEN.name)],
                     [InlineKeyboardButton("Sortir", callback_data=DeckConv.CANCEL.name)]]
        return keyboard
        
    @restrict(UserType.PLAYER)
    @run_async
    def new_deck(self, update, context):
        """/deck
        NFC Scan each player deck turn by turn
        Use InlineKeyboardMarkup to correct a card or submit your deck or see stats about it
        """
        user = update.message.from_user
        # Remove entry point to ensure one user is scanning only
        if self.current_user and user != self.current_user:
            text = f"{self.current_user.name} est déjà en train de scanner."
            context.bot.send_message(chat_id=user.id,
                                     text=text)
            return False

        # Avoid the same user to restart the scanning process
        elif self.current_user and user.id == self.current_user.id:
            text = f"{self.current_user.name}, continue de scanner."
            context.bot.send_message(chat_id=user.id,
                                     text=text)
            return False

        # Avoid multiple deck per user
        elif user in self.user_scanned:
            text = f"{self.current_user.name}, tu as déjà scanné ton deck."
            context.bot.send_message(chat_id=user.id,
                                     text=text)
            return False

        # Prepare message
        logging.info(f"{user} starts scanning his deck")
        self.current_user = session.query(Player).filter(Player.id==user.id).first()
        self.deck = Deck(player=self.current_user, name=f"Deck de {self.current_user.name}", game=self.game)
        text = f"Yo {self.current_user.name}, commence à scanner les cartes que tu as drafté !"
        reply_markup = InlineKeyboardMarkup(self.get_scan_keyboard(len(self.deck.cards)))
        message = context.bot.send_message(chat_id=user.id,
                                           text=text,
                                           reply_markup=reply_markup)
        context.dispatcher.add_handler(self.scan_buttons_handler)
        
        # Start scanning
        self.loop = True
        while self.loop:
            # Check if a card is available to read
            uid = self.pn532.read_passive_target(timeout=0.2)
            # Try again if no card is available.
            if uid is None:
                continue
            # Check if uid is known
            card = next((c.card for c in self.cubelist if c.uid == uid), None)
            if not card:
                # unknown card detected
                reply_markup = InlineKeyboardMarkup(self.get_scan_keyboard(len(self.deck.cards)))
                context.bot.editMessageText(chat_id=user.id,
                                            message_id=message.message_id,
                                            text="Carte non reconnue, continue à scanner",
                                            reply_markup=reply_markup)
            # Check if card is already scanned
            # TODO: verify if card is not scanned in another deck
            elif not card in self.deck.cards:
                self.deck.cards.append(card)
                edit = f"Continue à scanner...\nCartes scannées (len(self.deck.cards)):"
                for card in self.deck.cards:
                    edit += f"\n- {card.name}"
                reply_markup = InlineKeyboardMarkup(self.get_scan_keyboard(len(self.deck.cards)))
                context.bot.editMessageText(chat_id=user.id,
                                            message_id=message.message_id,
                                            text=edit,
                                            reply_markup=reply_markup)
                               

    def scan_buttons(self, update, context):
        """ InlineKeyboardMarkup response 4 types
        - Cancel conv
        - Remove last scanned card
        - See stats about your scanned deck
        - Submit and save scanned cards
        """
        query = update.callback_query
        if query.data == "0":
            # Cancel is called
            text = "Scan annulé, ton deck n'a pas été enregistré.\n"\
                   "Pour recommencer: /deck"
            query.edit_message_text(text=text,
                                    parse_mode="HTML")
            self.reset_state(context.dispatcher)
            
        if query.data == "1" and self.deck.cards:
            # Remove last element of decklist
            del self.deck.cards[-1]
            edit = f"Cartes scannées ({len(self.deck.cards)}):"
            for card in self.deck.cards:
                edit += f"\n- {card.name}"
            reply_markup = InlineKeyboardMarkup(self.get_scan_keyboard(len(self.deck.cards)))
            query.edit_message_text(text=edit,
                                    reply_markup=reply_markup)

        elif query.data == "2":
            # Send stats
            deck = "1x "+"\n1x ".join([c.name for c in self.deck.cards])
            deck_url = deckstat.get_deck_url(deck=deck, deck_name=self.deck.name)                
            text = f"Voici ton pool de carte : <a href='{deck_url}'>Voir mon deck</a>."
            reply_markup = InlineKeyboardMarkup(self.get_scan_keyboard(len(self.deck.cards)))
            query.edit_message_text(text=edit,
                                    reply_markup=reply_markup,
                                    parse_mode="HTML")

        elif query.data == "3":
            # Submit decklist
            text = "J'ai bien sauvegardé ton deck, pour le modifier "\
                   "ou consulter des infos le concernant: /mydeck"
            query.edit_message_text(text=text,
                                    parse_mode="HTML")
            context.user_data["deck"] = self.deck
            # Add card to 'scanned list' to avoid another player to scan this card
            self.scanned += self.deck.cards
            self.user_scanned.append(query.from_user)
            self.game.decks.append(self.deck)
            session.commit()
            logging.info(f"{self.deck} saved")
            self.reset_state(context.dispatcher)
        
        return False
    
    def deck_conv_handler(self):
        """Get ConversationHandler for deck management"""
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('mydeck', self.set_deck)],
            states={
                DeckConv.ACTION: [CallbackQueryHandler(self.get_deck_action)],
                DeckConv.NAME: [MessageHandler(Filters.text & (~ Filters.command), self.set_deck_name)],
                DeckConv.DESCR: [MessageHandler(Filters.text & (~ Filters.command), self.set_deck_desc)],
                DeckConv.STATS: [CallbackQueryHandler(self.get_deck_action)],
                DeckConv.TOKEN: [CallbackQueryHandler(self.get_deck_action)],
                DeckConv.NOTE: [MessageHandler(Filters.text & (~ Filters.command), self.set_card_note)],
                DeckConv.CARDS: [MessageHandler(Filters.text & (~ Filters.command), self.set_deck_cards)]
                },
            fallbacks=[CommandHandler('stop', self.stop)],
            per_user=True)

        return conv_handler
    
    def set_deck(self, update, context):
        """/mydeck Send options for managing your deck:
        - Set deck name (default Deck_de_Player)
        - Set deck description
        - Set note for a card (to be revealed during the game) only available before the game start
        - See tokens related to your deck
        - Add or remove cards
        - See stats on deckstat.net
        This handler is available once you have created a deck and until end of the game
        """
        # Check if user has a deck
        if not context.user_data.get("deck", None):
            text = "Tu n'as pas encore sauvegardé de deck."
            update.message.reply_text(text=text)
            return ConversationHandler.END
        
        # Modify : deck name, title, content, see stats...
        text = f"Titre: {context.user_data['deck'].name}\n" \
               f"Description: {context.user_data['deck'].description}\n" \
               f"Que souhaites-tu voir ou modifier dans ton deck ?"
        update.message.reply_text(text=text,
                                  reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()))
        return DeckConv.ACTION

    def get_deck_action(self, update, context):
        """InlineKeyboardMarkup response"""
        query = update.callback_query
        
        if query.data == DeckConv.NAME.name:
            name = context.user_data['deck'].name
            text = f"Le nom actuel de ton deck est <b>{name}</b>, "\
                     "envoie moi un nouveau nom pour ton deck. (/stop pour quitter)"
            query.edit_message_text(text=text,
                                    parse_mode="HTML")
            return DeckConv.NAME
        
        elif query.data == DeckConv.DESCR.name:
            description = context.user_data['deck'].description
            text = f"La description actuelle de ton deck est {'<b>' + description + '</b>' if description else 'vide' }, "\
                     "envoie moi une nouvelle description pour ton deck. (/stop pour quitter)"
            query.edit_message_text(text=text,
                                    parse_mode="HTML")
            return DeckConv.DESCR
        
        elif query.data == DeckConv.NOTE.name:
            text = "Envoie moi les cartes (les premières lettres de la cartes suffisent) "\
                   "auxquelles tu souhaites ajouter une note sous cette forme (/stop pour quitter):\n"\
                   "Urza (ma note)\nRichard (ma 2e note)"
            query.edit_message_text(text=text,
                                    parse_mode="HTML")
            return DeckConv.NOTE
        
        elif query.data == DeckConv.CARDS.name:
            text = "Envoie moi les cartes (les premières lettres de la cartes suffisent) "\
                   "que tu souhaites ajouter ou retirer sous cette forme (/stop pour quitter):\n"\
                   "+ Urza \n- Richard\n+3 Plains\n-2 Island"
            query.edit_message_text(text=text,
                                    parse_mode="HTML")
            return DeckConv.CARDS
        
        elif query.data == DeckConv.STATS.name:
            deck = session.query(Deck).filter(Deck.id==context.user_data["deck"].id).first()
            if deck.cards:
                url = deckstat.get_deck_url(deck)
                if url:
                    text = f"Voici ton pool de carte : <a href='{url}'>Voir mon deck</a>."
                else:
                    text = "Le site deckstat ne répond pas."
            else:
                text = "Ton deck ne comporte aucune carte pour le moment"
            
            query.edit_message_text(text=text,
                                    reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                    parse_mode="HTML")
            return DeckConv.ACTION
            
        elif query.data == DeckConv.TOKEN.name:
            deck = session.query(Deck).filter(Deck.id==context.user_data["deck"].id).first()
            text = "Voici la liste des tokens dont tu auras besoin:\n"
            count = 0
            for card in deck.cards:
                for token in card.tokens:
                    count += 1
                    text+= f"- <a href='{token.image_url}'>{token.power}/{token.toughness} {token.color} {token.name}</a>\n"
            if not count:
                text = "Ton deck n'a pas besoin de tokens"
            query.edit_message_text(text=text,
                                    reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                    disable_web_page_preview=True,
                                    parse_mode="HTML")
            return DeckConv.ACTION
        
        elif query.data == DeckConv.CANCEL.name:
            text = "Pour modifier ou voir de nouveau ton deck: /mydeck"
            query.edit_message_text(text=text,
                                    parse_mode="HTML")
            return ConversationHandler.END
        
    def set_deck_name(self, update, context):
        context.user_data['deck'].name = update.message.text
        text = f"Ok, ton deck se dénomme désormais <b>{context.user_data['deck'].name}</b>."        
        update.message.reply_text(text=text,
                                  reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                  parse_mode="HTML")
        return DeckConv.ACTION

    def set_deck_desc(self, update, context):
        context.user_data['deck'].description = update.message.text
        text = f"Ok, la description de ton deck est désormais :\n{context.user_data['deck'].description}."
        update.message.reply_text(text=text,
                                  reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                  parse_mode="HTML")
        return DeckConv.ACTION
    
    def set_card_note(self, update, context):
        answer = update.message.text
        regex = r"(.*)\((.*?)\)"
        r = re.compile(regex)
        matches = r.findall(answer)
        errors = []
        for cardname, note in matches:
            try:
                card = session.query(Card).filter(Card.name.like(cardname.strip() + "%")).one()
            except MultipleResultsFound:
                errors.append((cardname, "plusieurs cartes trouvées"))
                continue
            except NoResultFound:
                errors.append((cardname, "pas de carte trouvée"))
                continue
            if card in context.user_data['deck'].cards:
                session.query(DeckList).filter(DeckList.card_id == card.id, DeckList.deck_id == context.user_data["deck"].id).first().note = note
            else:
                errors.append((cardname, "carte absente du deck"))
        session.commit()
        text = "J'ai bien modifié le contenu de ton deck."
        if errors:
            text +=  " Cependant j'ai un problème avec les cartes suivantes:"
            for cardname, error in errors:
                text += f"\n- {cardname} ({error})"
        update.message.reply_text(text=text,
                                  reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()))
        return DeckConv.ACTION
    
    def set_deck_cards(self, update, context):
        answer = update.message.text
        regex = r"([+-])(\d?) (.*)"
        r = re.compile(regex)
        matches = r.findall(answer)
        errors = []
        for mode, num, cardname in matches:
            if not num:
                num = 1
            else:
                num = int(num)
            try:
                # Ameliorer avec un filtre sur le cube
                card = session.query(Card).filter(Card.name.like(cardname + "%")).one()
            except MultipleResultsFound:
                errors.append((cardname, "plusieurs cartes trouvées"))
                continue
            except NoResultFound:
                errors.append((cardname, "pas de carte trouvée"))
                continue
            if mode == "+":
                context.user_data['deck'].cards.append(card)
            elif mode == "-" and card in context.user_data['deck'].cards:
                context.user_data['deck'].cards.remove(card)
            else:
                errors.append((cardname, "carte absente du deck"))
        session.commit()
        text = "J'ai bien modifié le contenu de ton deck."
        if errors:
            text +=  " Cependant je n'ai pas trouvé les cartes suivantes:"
            for cardname, error in errors:
                text += f"\n- {cardname} ({error})"
        update.message.reply_text(text=text,
                                  reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()))
        return DeckConv.ACTION

    @restrict(UserType.PLAYER)
    def cancel(self, update, context):
        """Cancel conversation and reset states"""
        self.reset_state(context.dispatcher)

    def stop(self, update, context):
        text = "Pour modifier ou voir de nouveau ton deck: /mydeck"
        update.message.reply_text(text=text, reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
   
    def reset_state(self, dispatcher):
        """Reset state of all conversation variables and handlers"""
        # dispatcher.remove_handler(self.scan_handler)
        dispatcher.remove_handler(self.scan_buttons_handler)
        self.loop = False
        self.deck = None
        self.current_user = None

    def stop_deck_preparation(self, context):
        """reset all state and handlers and return game object"""
        # A user is scanning or has not finished is deck yet
        if self.current_user:
            return False
        # Nobody is scanning stop all DeckHandlers
        context.dispatcher.remove_handler(self.scan_handler)
        context.dispatcher.remove_handler(self.scan_buttons_handler)
        session.commit()
        return True
        # return self.game       

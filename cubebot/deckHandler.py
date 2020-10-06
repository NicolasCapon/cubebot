import re
import os
import logging
import json
import binascii
import config
import scryfall
import requests
from time import sleep
from datetime import datetime
import deckstat_interface as deckstat
from filters import restrict, UserType, DeckConv, GameStates
from model import session, Player, Deck, Card, CubeList, DeckList
from pn532 import PN532_SPI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, MessageEntity
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
        self.deck = None
        # self.user_scanned = []
        self.loop = False # Scan loop
        # Scanning device config
        self.pn532 = PN532_SPI(debug=False, reset=20, cs=4)
        self.pn532.SAM_configuration()
        
        # Handlers
        self.scan_handler = CommandHandler("scan", self.new_deck)
        dispatcher.add_handler(self.scan_handler)
        self.scan_buttons_handler = CallbackQueryHandler(self.scan_buttons)
        # Conversation Handler for deck title and description
        self.deck_conv_handler = self.deck_conv_handler()
        dispatcher.add_handler(self.deck_conv_handler)
    
    def get_scan_keyboard(self, count=0):
        if count:
            keyboard = [[InlineKeyboardButton("Corriger", callback_data='1'),
                        InlineKeyboardButton("Annuler", callback_data='0')],
                        [InlineKeyboardButton("Soumettre Deck", callback_data='2')]]
        else:
            keyboard = [[InlineKeyboardButton("Annuler", callback_data='0'),
                        InlineKeyboardButton("Soumettre", callback_data='2')]]
        return keyboard
        
    def get_deck_keyboard(self):
        """Get Keyboard depending of game and dialog state
        game state avoid modifying notes once the game is on
        """
        if self.game.state == GameStates.INIT.name :
                keyboard = [[InlineKeyboardButton("Nom", callback_data=DeckConv.NAME.name),
                         InlineKeyboardButton("Description", callback_data=DeckConv.DESCR.name)],
                         [InlineKeyboardButton("Cartes", callback_data=DeckConv.CARDS.name),
                         InlineKeyboardButton("Notes", callback_data=DeckConv.NOTE.name),
                         InlineKeyboardButton("Sign", callback_data=DeckConv.SIGN.name),
                         InlineKeyboardButton("Tokens", callback_data=DeckConv.TOKEN.name)],
                         [InlineKeyboardButton("Sortir", callback_data=DeckConv.CANCEL.name)]]
        else:
                keyboard = [[InlineKeyboardButton("Nom", callback_data=DeckConv.NAME.name),
                         InlineKeyboardButton("Description", callback_data=DeckConv.DESCR.name)],
                         [InlineKeyboardButton("Cartes", callback_data=DeckConv.CARDS.name),
                         InlineKeyboardButton("Sign", callback_data=DeckConv.SIGN.name),
                         InlineKeyboardButton("Tokens", callback_data=DeckConv.TOKEN.name)],
                         [InlineKeyboardButton("Sortir", callback_data=DeckConv.CANCEL.name)]]
        return keyboard
        
    @restrict(UserType.PLAYER)
    @run_async
    def new_deck(self, update, context):
        """/scan
        NFC Scan each player deck turn by turn
        Use InlineKeyboardMarkup to correct a card or submit your deck or see stats about it
        """
        user = update.message.from_user
        deck = self.game.get_deck_from_player_id(user.id)
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
        elif deck:
            logging.info(f"{user} starts scanning new cards for his existing deck")
            self.deck = deck
            self.current_user = session.query(Player).filter(Player.id==user.id).first()
        # elif user.id in self.user_scanned:
            # text = f"{self.current_user.name}, tu as déjà un deck chargé, utilise /mydeck pour le consulter."
            # context.bot.send_message(chat_id=user.id,
                                     # text=text)
            # return False
        else:
            logging.info(f"{user} starts scanning his new deck")
            self.current_user = session.query(Player).filter(Player.id==user.id).first()
            self.deck = Deck(player=self.current_user, name=f"Deck de {self.current_user.name}", game=self.game)
            session.add(self.deck)
        text = f"Yo {self.current_user.name}, commence à scanner tes cartes !"
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
            elif not any(card.id == deck_card.card_id for deck_card in self.deck.cards):
                DeckList(deck=self.deck, card=card)
                session.flush()
                edit = f"Continue à scanner...\nCartes scannées ({len(self.deck.cards)}):"
                for deck_card in self.deck.cards:
                    edit += f"\n- {deck_card.card.name}"
                reply_markup = InlineKeyboardMarkup(self.get_scan_keyboard(len(self.deck.cards)))
                context.bot.editMessageText(chat_id=user.id,
                                            message_id=message.message_id,
                                            text=edit,
                                            reply_markup=reply_markup)
                sleep(0.1) # Avoid spam limit
                               

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
                   "Pour recommencer: /scan"
            session.delete(self.deck)
            query.edit_message_text(text=text,
                                    parse_mode="HTML")
            self.reset_state(context.dispatcher)
            
        if query.data == "1" and self.deck.cards:
            # Remove last element of decklist
            # del self.deck.cards[-1]
            self.deck.cards.remove(self.deck.cards[-1])
##            session.delete(self.deck.cards[-1])
##            session.flush()
            edit = f"Cartes scannées ({len(self.deck.cards)}):"
            for deck_card in self.deck.cards:
                edit += f"\n- {deck_card.card.name}"
            reply_markup = InlineKeyboardMarkup(self.get_scan_keyboard(len(self.deck.cards)))
            query.edit_message_text(text=edit,
                                    reply_markup=reply_markup)

        elif query.data == "2":
            # Submit decklist
            text = "J'ai bien sauvegardé ton deck, pour le modifier "\
                   "ou consulter des infos le concernant:\n/mydeck"
            query.edit_message_text(text=text,
                                    parse_mode="HTML")
            context.user_data["deck"] = self.deck
            context.user_data["deckstat"] = deckstat.get_deck_url(self.deck)
            # Append user to list of player who already has scanned their deck
            # self.user_scanned.append(query.from_user.id)
            if not self.deck in self.game.decks:
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
                DeckConv.TOKEN: [CallbackQueryHandler(self.get_deck_action)],
                DeckConv.NOTE: [MessageHandler(Filters.text & (~ Filters.command), self.set_card_note)],
                DeckConv.CARDS: [MessageHandler(Filters.text & (~ Filters.command), self.set_deck_cards)],
                DeckConv.SIGN: [MessageHandler(Filters.text & (~ Filters.command), self.choose_card)],
                DeckConv.CONFIRM: [CallbackQueryHandler(self.confirm_card)],
                DeckConv.SENDING: [MessageHandler(Filters.audio | Filters.voice | Filters.entity(MessageEntity.URL) & (~ Filters.command), self.save_signature)]
                },
            fallbacks=[CommandHandler('stop', self.stop)],
            per_user=True)

        return conv_handler

    def get_deck_info(self, context):
        if context.user_data.get('deckstat', None):
            deckstat_text = f"<a href='{context.user_data['deckstat']}'>{context.user_data['deck'].name}</a>"
        else:
            deckstat_text = None
        text = f"Titre: {deckstat_text if deckstat_text else context.user_data['deck'].name}\n" \
               f"Description: {context.user_data['deck'].description if context.user_data['deck'].description else 'Aucune'}\n" \
               f"Nombres de cartes: {context.user_data['deck'].card_count}\n" \
               f"Que souhaites-tu voir ou modifier dans ton deck ?"
        return text
        
    def set_deck(self, update, context):
        """/mydeck Send options for managing your deck:
        - Set deck name (default Deck_de_Player)
        - Set deck description
        - Set note for a card (to be revealed during the game) only available before the game start
        - See tokens related to your deck
        - Add or remove cards
        This handler is available once you have created a deck and until end of the game
        """
        # Check if user has a deck
        deck = self.game.get_deck_from_player_id(update.message.from_user.id)
        if not deck:
            p = session.query(Player).filter(Player.id==update.message.from_user.id).first()
            d = Deck(player=p, name=f"Deck de {p.name}", game=self.game)
            self.game.decks.append(d)
            session.commit()
            context.user_data['deck'] = d
        elif not context.user_data.get("deck", None):
            # If user has deck but not in context_data, had it
            context.user_data['deck'] = deck
            context.user_data["deckstat"] = deckstat.get_deck_url(deck)

        # Send deck_editor menu
        context.bot.send_message(chat_id=update.message.from_user.id,
                                 text=self.get_deck_info(context),
                                 reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                 parse_mode="HTML")
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
        
        elif query.data == DeckConv.SIGN.name:
            text = "Envoie moi le nom (les premières lettres de la cartes suffisent) "\
                   "de la carte que tu souhaites signer."
            query.edit_message_text(text=text,
                                    parse_mode="HTML")
            return DeckConv.SIGN
            
        elif query.data == DeckConv.TOKEN.name:
            deck = session.query(Deck).filter(Deck.id==context.user_data["deck"].id).first()
            text = "Voici la liste des tokens dont tu auras besoin:\n"
            tokens = []
            for deck_card in deck.cards:
                for token in deck_card.card.tokens:
                    if token in tokens: continue
                    tokens.append(token)
                    if isinstance(token.power, int) and isinstance(token.toughness, int):
                        text+= f"- <a href='{token.image_url}'>{token.power}/{token.toughness} {token.color} {token.name}</a>\n"
                    else:
                        text+= f"- <a href='{token.image_url}'>{token.color} {token.name}</a>\n"
            if not tokens:
                text = "Ton deck n'a pas besoin de token.\n"
            text += self.get_deck_info(context)
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
        text = f"Modification sauvegardée.\n" + self.get_deck_info(context)
        update.message.reply_text(text=text,
                                  reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                  parse_mode="HTML")
        return DeckConv.ACTION

    def set_deck_desc(self, update, context):
        context.user_data['deck'].description = update.message.text
        text = f"Modification sauvegardée.\n" + self.get_deck_info(context)
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
        modif = 0
        for cardname, note in matches:
            try:
                card = session.query(Card).filter(Card.name.like(cardname.strip() + "%")).one()
            except MultipleResultsFound:
                errors.append((cardname, "plusieurs cartes trouvées"))
                continue
            except NoResultFound:
                errors.append((cardname, "pas de carte trouvée"))
                continue
            if any(card.id == deck_card.card_id for deck_card in context.user_data['deck'].cards):
                deck_card = session.query(DeckList).filter(DeckList.card_id == card.id, DeckList.deck_id == context.user_data["deck"].id).first()
                deck_card.note = note
                modif += 1
            else:
                errors.append((cardname, "carte absente du deck"))
        session.commit()
        if modif: context.user_data['deckstat'] = deckstat.get_deck_url(context.user_data['deck'])
        text = "J'ai bien modifié les notes de ton deck."
        if errors:
            text +=  " Cependant j'ai un problème avec les cartes suivantes:"
            for cardname, error in errors:
                text += f"\n- {cardname} ({error})"
        text += "\n" + self.get_deck_info(context)
        update.message.reply_text(text=text,
                                  reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                  parse_mode="HTML")
        return DeckConv.ACTION

    def choose_card(self, update, context):
        answer = update.message.text
        c = session.query(CubeList).join(Card).join(DeckList).join(Deck).filter(Deck.player_id==update.message.from_user.id,
                                                                 Deck.game_id==self.game.id,
                                                                 Card.name.like(f"{answer}%")).first()
        avert = "Attention cette carte est déjà signée !\n"
        if c:
            if c.signature:
                path = os.path.join(config.src_dir, "resources", "sounds", c.signature)
                filename, file_extension = os.path.splitext(path)
                audio_formats = [".mp3", ".m4a"]
                voice_formats = [".ogg", ".oga"]
                if file_extension in audio_formats:
                    context.bot.sendAudio(chat_id=update.message.chat_id,
                                          audio=open(path, 'rb'),
                                          title=c.card.name,
                                          performer="cubebot")
                elif file_extension in voice_formats:
                    context.bot.sendVoice(chat_id=update.message.chat_id,
                                          voice=open(path, 'rb'),
                                          caption=c.card.name)
            text = f"{avert if c.signature  else ''}{c.card.name} - Est-ce bien ta carte ?"
            keyboard = [[InlineKeyboardButton("Annuler", callback_data='0'),
                         InlineKeyboardButton("Retenter", callback_data='2')],
                        [InlineKeyboardButton("Oui", callback_data='1')]]
            markup = InlineKeyboardMarkup(keyboard)
            context.user_data["sign_card_id"] = c.card.id
            update.message.reply_text(text=text,
                                      reply_markup=markup)
            return DeckConv.CONFIRM

        else:
            text = "Je n'ai pas trouvé cette carte dans ton deck.\n" + self.get_deck_info(context)
            update.message.reply_text(text=text,
                                      reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                      parse_mode="HTML")
            return DeckConv.ACTION

    def confirm_card(self, update, context):
        query = update.callback_query
        if query.data == "1":
            text = "Ok, envoie moi un fichier audio pour signer ta carte.\n(/stop pour sortir)"
            query.edit_message_text(text=text)
            return DeckConv.SENDING

        elif query.data == "2":
            text = "No problemo, renvoie moi le nom de ta carte.\n(/stop pour sortir)"
            query.edit_message_text(text=text)
            return DeckConv.SIGN

        else:
            text = "Comme tu voudras.\n" + self.get_deck_info(context)
            query.edit_message_text(text=text,
                                    reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                    parse_mode="HTML")
            return DeckConv.ACTION

    def save_signature(self, update, context):
        card_id = context.user_data["sign_card_id"]
        if update.message.audio:
            file = context.bot.getFile(update.message.audio.file_id)
            filename, file_extension = os.path.splitext(file.file_path)
            dl_path = os.path.join(config.src_dir,"resources",
                                   "sounds",f"{self.game.cube.id}_{card_id}{file_extension}")
            file.download(dl_path)
            logging.info(f"audio downloaded ({dl_path})")

        elif update.message.voice:
            file = context.bot.getFile(update.message.voice.file_id)
            filename, file_extension = os.path.splitext(file.file_path)
            dl_path = os.path.join(config.src_dir,"resources",
                                   "sounds",f"{self.game.cube.id}_{card_id}{file_extension}")
            file.download(dl_path)
            logging.info(f"audio downloaded ({dl_path})")

        elif update.message.entities:
            url = update.message.parse_entity(update.message.entities[0])
            filename, file_extension = os.path.splitext(url)
            logging.info(f"Audio to download from url: {url}")
            audio_formats = [".mp3", ".m4a", ".ogg"]
            if file_extension in audio_formats: 
                r = requests.get(url)
                if r.ok:
                    dl_path = os.path.join(config.src_dir,"resources",
                                   "sounds",f"{self.game.cube.id}_{card_id}{file_extension}")
                    with open(dl_path, 'wb') as f:
                        f.write(r.content)
                        logging.info(f"audio downloaded ({dl_path})")
                else:
                    text = f"Le serveur répond avec un code d'erreur {r.status_code}. Essaye avec un autre lien."
                    update.message.reply_text(text=text)
                    return DeckConv.SENDING
            else:
                text = f"Format audio inconnu. Essaye avec un autre lien avec l'un des formats suivant {audio_formats}."
                update.message.reply_text(text=text)
                return DeckConv.SENDING

        else:
            text = "Format audio inconnu. Essayes-en un autre.\n(/stop pour sortir)"
            update.message.reply_text(text=text)
            return DeckConv.SENDING
        
        c = session.query(CubeList).filter(CubeList.card_id==card_id, CubeList.cube_id==self.game.cube.id).first()
        c.signature = f"{self.game.cube.id}_{card_id}{file_extension}"
        session.commit()
        logging.info(f"{c} signed with {c.signature}")
        text = "Ta carte est desormais signée.\n" + self.get_deck_info(context)
        update.message.reply_text(text=text,
                                  reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                  parse_mode="HTML")
        return DeckConv.ACTION
        
    def set_deck_cards(self, update, context):
        answer = update.message.text
        if answer == "REMOVE ALL CARDS":
            context.user_data['deck'].cards[:] = []
            session.commit()
            context.user_data['deckstat'] = None
            text = "Jai bien supprimé toutes les cartes de ton deck.\n"
            update.message.reply_text(text=text+self.get_deck_info(context),
                                      reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                      parse_mode="HTML")
            return DeckConv.ACTION
        
        regex =  r"([+/-])? ?(\d*?) ?(\[.*\])? (.*)" # r"([+-])(\d?) (.*)"      (([+-]?)(\d*?) (\[.*\]) ?)?([a-zA-Z].*)
        reg = re.compile(regex)
        errors = []
        modif = 0
        for line in answer.split("\n"):
            # Import note after # mark with this syntax: 1 [CN2] Arcane Savant #Summon the pack
            note = None
            s_note = line.split(' #', 1)
            if len(s_note) == 2:
                line = s_note[0]
                note = s_note[1]
                print(note)
            matches = reg.findall(line)
            if matches:
                mode, num, set_code, cardname = matches[0]
            else:
                errors.append((line, "expression non reconnue"))
                continue
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
            if mode == "" or mode == "+":
                context.user_data['deck'].add_card(card=card, amount=num, note=note)
                modif += 1
            elif mode == "-":
                r = context.user_data['deck'].remove_card(card, num)
                if not r: errors.append((cardname, "carte absente du deck"))
                modif += 1
        session.commit()
        print(context.user_data['deck'].cards)
        text = "J'ai bien modifié le contenu de ton deck."
        if errors:
            text +=  " Cependant je n'ai pas trouvé les cartes suivantes:"
            for cardname, error in errors:
                text += f"\n- {cardname} ({error})"
        if modif: context.user_data['deckstat'] = deckstat.get_deck_url(context.user_data['deck'])
        text += "\n" + self.get_deck_info(context)
        update.message.reply_text(text=text,
                                  reply_markup=InlineKeyboardMarkup(self.get_deck_keyboard()),
                                  parse_mode="HTML")
        return DeckConv.ACTION

    def stop(self, update, context):
        text = "Pour modifier ou voir de nouveau ton deck: /mydeck"
        update.message.reply_text(text=text, reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
   
    def reset_state(self, dispatcher):
        """Reset state of all conversation variables and handlers"""
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

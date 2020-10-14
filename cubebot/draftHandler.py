import re
import deckstat_interface as deckstat
from time import sleep
from random import shuffle
from filters import restrict, SealedConv, UserType
from functools import partial
from model import session, Cube, CubeList, Game, Player, Card, Deck, DeckList, Draft, Drafter
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, ReplyKeyboardRemove
from telegram.ext import Filters, CommandHandler, ConversationHandler, MessageHandler, CallbackQueryHandler

class DraftHandler():

    def __init__(self, dispatcher):
        self.dispatcher = dispatcher
        self.players = []
        self.subscribers = []
        # Draft
        self.draft = None
        self.drafted_card_handler = None
        self.draft_handler = self.get_select_player_convHandler("draft", self.start_draft)
        dispatcher.add_handler(self.draft_handler)
        """TODO: mutualiser le select player handler"""
        # Sealed
        self.sealed_handler = self.get_select_player_convHandler("sealed", self.start_sealed)
        dispatcher.add_handler(self.sealed_handler)
        
    def get_select_player_convHandler(self, command, behaviour):
        
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler(command, self.start_select_player)],
            states={
                SealedConv.CHOOSING: [CallbackQueryHandler(partial(self.choose_player, behaviour))]
                },
            fallbacks=[])

        return conv_handler
    
    def get_select_player_keyboard(self, players, subscribers):
        # subscribers = sealed_players
        keyboard = []
        if len(subscribers) < 4:
            for player in players:
                if player not in subscribers:
                    keyboard.append([InlineKeyboardButton(player.name, callback_data=player.id)])
        if len(subscribers):
            keyboard.append([InlineKeyboardButton("Corriger", callback_data="-1"),
                             InlineKeyboardButton("Envoyer", callback_data="1")])
        keyboard.append([InlineKeyboardButton("Annuler", callback_data="0")])
        return keyboard
            
    @restrict(UserType.ADMIN)
    def start_select_player(self, update, context):
        self.players = session.query(Player).all()
        reply_markup = InlineKeyboardMarkup(self.get_select_player_keyboard(self.players, self.subscribers))
        text = "Selectionne les joueurs qui participeront :"
        message = update.message.reply_text(text=text,
                                            reply_markup=reply_markup)
        return SealedConv.CHOOSING
        
    def choose_player(self, behaviour, *args):
        update, context = args
        query = update.callback_query
        
        if query.data == "0":
            text = "Limité annulé, pour recommencer: /sealed"
            query.edit_message_text(text=text)
            return ConversationHandler.END
        
        elif query.data == "1":
            # players are selected, start something
            text = "Joueurs selectionnés:\n"
            for player in self.subscribers:
                text += f"- <a href='tg://user?id={player.id}'>{player.name}</a>\n"
            query.edit_message_text(text=text, parse_mode="HTML")
            behaviour(update, context)
            self.subscribers = []
            return ConversationHandler.END
        
        elif query.data == "-1":
            # Remove last
            del self.subscribers[-1]
            text = "Joueurs selectionnés:\n"
            for player in self.subscribers:
                text += f"- <a href='tg://user?id={player.id}'>{player.name}</a>\n"
        
        else:
            # Add player
            player = session.query(Player).filter(Player.id == int(query.data)).first()
            self.subscribers.append(player)
            text = "Joueurs selectionnés:\n"
            for player in self.subscribers:
                text += f"- <a href='tg://user?id={player.id}'>{player.name}</a>\n"
        
        reply_markup = InlineKeyboardMarkup(self.get_select_player_keyboard(self.players, self.subscribers))
        query.edit_message_text(text=text,
                                parse_mode="HTML",
                                reply_markup=reply_markup)
        return SealedConv.CHOOSING
        
    def start_sealed(self, update, context):
        # Send sealed
        cards = session.query(Card).join(CubeList).join(Cube).filter(Cube.id == 1, Card.type_line != "Basic Land").all()
        shuffle(cards)
        sealed_size = 90
        start = 0
        final_text = "Les scellés ont bien été envoyés à :\n"
        for player in self.subscribers:
            pool = cards[start:start+sealed_size]
            start += sealed_size
            url = deckstat.get_sealed_url(pool, player)
            logging.info(f"{player.name} Sealed Pool [{url}]")
            text = f"{player.name} voici <a href='{url}'>ton scellé</a>.\nPense à créer ton deck avec et à le sauvegarder avant la prochaine partie.\n"
            text += "<i>Pour modifier ton deck utilise l'éditeur deckstat puis enregistre le sur ton compte "\
                    "ou si tu n'as pas de compte fait les modifs sur deckstat puis clique sur export et copie colle ta decklist terminée dans le chat.</i>"
            context.bot.send_message(chat_id=player.id,
                                     text=text,
                                     parse_mode="HTML")
            final_text += f"- {player.name}\n"
            sleep(1)
        
        update.callback_query.edit_message_text(text=final_text)

    def get_booster_dialogue(self, drafter, card_choice=None, is_new_booster=True, row_length=3):
        text = f"<u>Ronde {self.draft.round_count}/{self.draft.round_num}</u>"
        if drafter.pool:
            text += f"\nMon dernier pick: <a href='https://scryfall.com/card/{drafter.pool[-1].scryfall_id}'>{drafter.pool[-1].name}</a>"
        
        url = drafter.data["deckstats"]
        if url:
            emoji_chart = "\U0001F4CA"
            text += f"\nVoir mon pool: <a href='{url}'>{emoji_chart}</a>"
        
        if is_new_booster or not card_choice:
            text += "\nSelectionne une carte :\n"
        else:
            text += "\nChoix pris en compte. En attente des autres joueurs...\n"
        
        booster = drafter.get_booster()
        if not booster:
            text = f"Draft terminé. Voici ton <a href='{url}'>pool</a>"
            # TODO : function to clean draft data and handlers
            self.draft = None
            self.dispatcher.remove_handler(self.drafted_card_handler)
            # Add entry point
            self.dispatcher.add_handler(self.draft_handler)
            logging.info("Draft ends")
            return text, None
        
        cards = booster.cards
        if not cards:
            text += "Pas de cartes à drafter pour le moment."
            return text, None
        
        choice_emoji = "\U0001F448"
        keyboard = []
        for i in range(0, len(cards), row_length):
            row = []
            max = i + row_length
            if max > len(cards): max = len(cards)
            for n in range(i, max, 1):
                if cards[n] == card_choice:
                    text += f"{n+1}) <b><a href='https://scryfall.com/card/{cards[n].scryfall_id}'>{cards[n].name}</a></b>{choice_emoji}\n"
                else:
                    row.append(InlineKeyboardButton(f"{n+1}", callback_data=f"card_id={cards[n].id}"))
                    text += f"{n+1}) <a href='https://scryfall.com/card/{cards[n].scryfall_id}'>{cards[n].name}</a>\n"
            keyboard.append(row)

        return text, InlineKeyboardMarkup(keyboard)
        
        
    def start_draft(self, update, context):
        # Remove entry point
        self.dispatcher.remove_handler(self.draft_handler)
        cube = session.query(Cube).first()
        self.draft = Draft(cube, round_num=3, booster_size=3)
        [self.draft.add_drafter(Drafter(s.id, s.name)) for s in self.subscribers]
        self.drafted_card_handler = CallbackQueryHandler(self.choose_card, pattern=r"card_id=(\d*)")
        self.dispatcher.add_handler(self.drafted_card_handler)
        self.draft.start()

        for drafter in self.draft.drafters:
            drafter.data = {"query": None, "deckstats": None}
            text, reply_markup = self.get_booster_dialogue(drafter)
            context.bot.send_message(chat_id=drafter.id,
                                     text=text,
                                     reply_markup=reply_markup,
                                     parse_mode="HTML",
                                     disable_web_page_preview=True)
    
    
    def choose_card(self, update, context):
        query = update.callback_query
        drafter = self.draft.get_drafter_by_id(query.from_user.id)
        reg = re.compile(r"card_id=(\d*)")
        match = int(reg.findall(query.data)[0])
        card = session.query(Card).filter(Card.id == match).first()
        is_new_booster, is_new_round = drafter.choose(card)
        drafter.data["query"] = query
        
        # If new booster or new round, we update reply markup for all drafters
        if is_new_booster or is_new_round:
            for drafter in self.draft.drafters:
                if len(drafter.pool) > 1:
                    drafter.data["deckstats"] = deckstat.get_sealed_url(drafter.pool, drafter)
                    sleep(0.5)
                text, reply_markup = self.get_booster_dialogue(drafter, is_new_booster=is_new_booster)
                drafter.data["query"].edit_message_text(text=text,
                                                        reply_markup=reply_markup,
                                                        parse_mode="HTML",
                                                        disable_web_page_preview=True)
        else:
            text, reply_markup = self.get_booster_dialogue(drafter, card, is_new_booster)
            query.edit_message_text(text=text,
                                    reply_markup=reply_markup,
                                    parse_mode="HTML",
                                    disable_web_page_preview=True)
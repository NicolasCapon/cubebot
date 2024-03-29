﻿import re
import io
import deckstat_interface as deckstat
import logging
from utils import set_boosters
from time import sleep
from random import shuffle
from filters import restrict, SealedConv, UserType
from functools import partial
from model import session, Cube, CubeList, Game, Player, Card, Deck, DeckList, Draft, Drafter
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, ReplyKeyboardRemove
from telegram.ext import Filters, CommandHandler, ConversationHandler, MessageHandler, CallbackQueryHandler


class DraftHandler:

    def __init__(self, dispatcher):
        self.dispatcher = dispatcher
        self.players = []
        self.subscribers = []
        self.cube = None
        # Draft
        self.draft = None
        self.drafted_card_handler = None
        self.draft_pool_handler = None
        self.draft_handler = self.get_select_player_convHandler("draft", self.start_draft)
        dispatcher.add_handler(self.draft_handler)
        # Sealed
        self.sealed_handler = self.get_select_player_convHandler("sealed", self.start_sealed)
        dispatcher.add_handler(self.sealed_handler)
        
    def get_select_player_convHandler(self, command, behaviour):
        
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler(command, self.start_select_cube)],
            states={
                SealedConv.CUBE: [CallbackQueryHandler(self.choose_cube, pattern=r"cube_id=(\d*)$")],
                SealedConv.CHOOSING: [CallbackQueryHandler(partial(self.choose_player, behaviour),
                                                           pattern=r"player_id=(\d*)$")]
                },
            fallbacks=[])

        return conv_handler
    
    def get_select_player_keyboard(self):
        # subscribers = sealed_players
        keyboard = []
        if len(self.subscribers) < 5:
            for player in self.players:
                if player not in self.subscribers:
                    keyboard.append([InlineKeyboardButton(player.name, callback_data=f"player_id={player.id}")])
        if len(self.subscribers):
            keyboard.append([InlineKeyboardButton("Corriger", callback_data="player_id=2"),
                             InlineKeyboardButton("Envoyer", callback_data="player_id=1")])
        keyboard.append([InlineKeyboardButton("Annuler", callback_data="player_id=0")])
        return keyboard

    @restrict(UserType.ADMIN)
    def start_select_cube(self, update, context):
        keyboard = []
        cubes = session.query(Cube).all()
        for cube in cubes:
            keyboard.append([InlineKeyboardButton(cube.name, callback_data=f"cube_id={cube.id}")])
        keyboard.append([InlineKeyboardButton("Annuler", callback_data="cube_id=0")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = "Selectionne un cube :"
        update.message.reply_text(text=text,
                                  reply_markup=reply_markup)
        return SealedConv.CUBE

    def choose_cube(self, update, context):
        query = update.callback_query
        reg = re.compile(r"cube_id=(\d*)")
        match = int(reg.findall(query.data)[0])

        if match == 0:
            text = "Limité annulé, pour recommencer: /sealed ou /draft"
            query.edit_message_text(text=text)
            return ConversationHandler.END

        else:
            self.cube = session.query(Cube).filter(Cube.id == match).one()
            # TODO: load here cube specific draft behaviour
            self.players = session.query(Player).all()
            reply_markup = InlineKeyboardMarkup(self.get_select_player_keyboard())
            text = f"Cube sélectionné: {self.cube.name}\nSélectionne maintenant les joueurs qui participeront :"
            query.edit_message_text(text=text,
                                    reply_markup=reply_markup)
            return SealedConv.CHOOSING
        
    def choose_player(self, behaviour, *args):
        update, context = args
        query = update.callback_query
        reg = re.compile(r"player_id=(\d*)")
        match = reg.findall(query.data)[0]
        
        if match == "0":
            text = "Limité annulé, pour recommencer: /sealed ou /draft"
            query.edit_message_text(text=text)
            self.subscribers = []
            return ConversationHandler.END
        
        elif match == "1":
            # players are selected, start something
            text = "Joueurs selectionnés:\n"
            for player in self.subscribers:
                text += f"- <a href='tg://user?id={player.id}'>{player.name}</a>\n"
            query.edit_message_text(text=text, parse_mode="HTML")
            behaviour(update, context)
            self.subscribers = []
            return ConversationHandler.END
        
        elif match == "2":
            # Remove last
            del self.subscribers[-1]
            text = "Joueurs selectionnés:\n"
            for player in self.subscribers:
                text += f"- <a href='tg://user?id={player.id}'>{player.name}</a>\n"
        
        else:
            # Add player
            player = session.query(Player).filter(Player.id == int(match)).first()
            self.subscribers.append(player)
            text = "Joueurs selectionnés:\n"
            for player in self.subscribers:
                text += f"- <a href='tg://user?id={player.id}'>{player.name}</a>\n"
        
        reply_markup = InlineKeyboardMarkup(self.get_select_player_keyboard())
        query.edit_message_text(text=text,
                                parse_mode="HTML",
                                reply_markup=reply_markup)
        return SealedConv.CHOOSING
        
    def start_sealed(self, update, context):
        # Send sealed
        cards = session.query(Card).join(CubeList).join(Cube).filter(Cube.id == self.cube.id, Card.type_line != "Basic Land").all()
        shuffle(cards)
        shuffle(self.subscribers)
        sealed_size = 90
        start = 0
        final_text = "Les scellés ont bien été envoyés à :\n"
        for player in self.subscribers:
            pool = cards[start:start+sealed_size]
            start += sealed_size
            url = deckstat.get_sealed_url(pool, title=f"Scellé de {player.name}")
            logging.info(f"{player.name} Sealed Pool [{url}]")
            text = f"{player.name} voici <a href='{url}'>ton scellé</a>.\nPense à créer ton deck avec et à le sauvegarder avant la prochaine partie.\n"
            text += "<i>Pour modifier ton deck utilise l'éditeur deckstat puis enregistre le sur ton compte "\
                    "ou si tu n'as pas de compte fait les modifs sur deckstat puis cliques sur export et copie colle ta decklist terminée dans le chat.</i>"
            context.bot.send_message(chat_id=player.id,
                                     text=text,
                                     parse_mode="HTML")
            final_text += f"- {player.name}\n"
            sleep(1)
        
        update.callback_query.edit_message_text(text=final_text)

    def get_booster_dialogue(self, drafter, is_new_booster=True, row_length=3):
        text = f"Un booster tout frais est disponible !\n\n"
        booster = drafter.get_booster()
        if booster and booster.from_drafter:
            text = f"<a href='tg://user?id={booster.from_drafter.id}'>{booster.from_drafter.name}</a> vient de te passer son booster !\n\n"
        
        text += f"<u>Ronde {self.draft.round_count}/{self.draft.round_num}</u>"
        if drafter.pool:
            text += f"\nMon dernier pick: <a href='https://scryfall.com/card/{drafter.pool[-1].scryfall_id}'>{drafter.pool[-1].name}</a>"
        
        if len(drafter.pool) > 1:
            text += f"\nVoir mon pool: /pool"
        
        if is_new_booster or not drafter.choice:
            text += "\nSelectionne une carte :\n"
        else:
            text += "\nChoix pris en compte. En attente des autres joueurs...\n"
        
        if not booster:
            session.commit()
            url = deckstat.get_sealed_url(drafter.pool, title=f"Draft de {drafter.name}")
            text = f"Draft terminé. Voici ton <a href='{url}'>pool</a>"
            # TODO : function to clean draft data and handlers
            if self.drafted_card_handler:
                self.dispatcher.remove_handler(self.draft_pool_handler)
                self.dispatcher.remove_handler(self.drafted_card_handler)
                self.drafted_card_handler = None
                # Add entry point
                self.dispatcher.add_handler(self.draft_handler)
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
                if drafter.choice and cards[n] == drafter.choice.card:
                    text += f"{n+1}) <b><a href='{cards[n].image_url}'>{cards[n].name}</a></b>{choice_emoji}\n"
                else:
                    callback_data = f"[{self.get_callback_pattern(id_only=True)}]card_id={cards[n].id}"
                    row.append(InlineKeyboardButton(f"{n+1}", callback_data=callback_data))
                    text += f"{n+1}) <a href='{cards[n].image_url}'>{cards[n].name}</a>\n"
            keyboard.append(row)

        return text, InlineKeyboardMarkup(keyboard)

    def get_callback_pattern(self, id_only=False):
        # pattern example: [3124]card_id=208
        # If pattern is False return only the id
        i = f"{self.draft.id}{self.draft.round_count}{self.draft.drafters[0].pick_count}"
        if id_only: return i
        p = r"^\[" + i + r"\]card_id=(\d*)$"
        logging.info(f"Callback pattern: {p}")
        return p

    def start_draft(self, update, context):
        # Remove entry point
        self.dispatcher.remove_handler(self.draft_handler)
        self.draft = Draft(cube=self.cube)
        [self.draft.add_drafter(Drafter(s.id, s.name)) for s in self.subscribers]
        remaining_cards, filename = set_boosters(self.draft)
        self.send_doc(chat_id=update.callback_query.from_user.id,
                      context=context,
                      content=remaining_cards,
                      filename=filename)
        self.draft.start()

        self.drafted_card_handler = CallbackQueryHandler(self.choose_card, pattern=self.get_callback_pattern())
        self.dispatcher.add_handler(self.drafted_card_handler)
        self.draft_pool_handler = CommandHandler("pool", self.get_drafter_pool)
        self.dispatcher.add_handler(self.draft_pool_handler)

        for drafter in self.draft.drafters:
            drafter.data = {"query": None}
            text, reply_markup = self.get_booster_dialogue(drafter)
            context.bot.send_message(chat_id=drafter.id,
                                     text=text,
                                     reply_markup=reply_markup,
                                     parse_mode="HTML",
                                     disable_web_page_preview=True,
                                     disable_notification=False)
        
    def choose_card(self, update, context):
        query = update.callback_query
        drafter = self.draft.get_drafter_by_id(query.from_user.id)
        reg = re.compile(r"card_id=(\d*)")
        match = int(reg.findall(query.data)[0])
        card = session.query(Card).filter(Card.id == match).first()
        pick_count = drafter.pick_count
        round_count = self.draft.round_count
        is_new_booster, is_new_round = drafter.choose(card)
        drafter.data["query"] = query
        
        # If new booster or new round, we edit previous query message then send new reply markup for all drafters
        if is_new_booster or is_new_round:
            # Update callback pattern to avoid an old callback to to send wrong data
            self.drafted_card_handler.pattern = self.get_callback_pattern()
            for drafter in self.draft.drafters:
                # If auto pick is activated, send the auto pick to drafter
                if is_new_round and self.draft.auto_pick_last_card:
                    self.send_card(drafter.pool[-2],
                                   msg_data=drafter.data["query"],
                                   title=f"Ronde {round_count} Pick {pick_count}")
                    self.send_card(drafter.pool[-1],
                                   msg_data=drafter.id,
                                   title=f"Ronde {round_count} Pick {pick_count+1}",
                                   context=context)
                else:
                    self.send_card(drafter.pool[-1],
                                   msg_data=drafter.data["query"],
                                   title=f"Ronde {round_count} Pick {pick_count}")

                text, reply_markup = self.get_booster_dialogue(drafter, is_new_booster=is_new_booster)
                sleep(0.5)
                context.bot.send_message(chat_id=drafter.id,
                                         text=text,
                                         reply_markup=reply_markup,
                                         parse_mode="HTML",
                                         disable_web_page_preview=True,
                                         disable_notification=False)
        # If a choice is made but not all users made one, we show choosed card
        else:
            text, reply_markup = self.get_booster_dialogue(drafter, is_new_booster)
            query.edit_message_text(text=text,
                                    reply_markup=reply_markup,
                                    parse_mode="HTML",
                                    disable_web_page_preview=True)
    
    def get_drafter_pool(self, update, context):
        text = "Il te faut au moins avoir drafté 2 cartes pour voir ton pool."
        drafter = self.draft.get_drafter_by_id(update.message.from_user.id)
        if len(drafter.pool) > 1:
            url = deckstat.get_sealed_url(drafter.pool, title=f"Draft de {drafter.name}")
            text = f"Voici <a href='{url}'>ton pool</a>."
        
        update.message.reply_text(text=text,
                                  parse_mode="HTML")

    @staticmethod
    def send_card(card, msg_data, title, context=None):
        text = f"<a href='{card.image_url}'>{title}</a>"#https://scryfall.com/card/
        if context:
            context.bot.send_message(chat_id=msg_data,
                                     text=text,
                                     parse_mode="HTML",
                                     disable_web_page_preview=False)
        else:
            msg_data.edit_message_text(text=text,
                                    parse_mode="HTML",
                                    disable_web_page_preview=False)

        sleep(0.5)

    @staticmethod
    def send_doc(chat_id, context, content, filename):
        s = io.StringIO(content)
        s.seek(0)
        document = io.BytesIO()
        document.write(s.getvalue().encode())
        document.seek(0)
        document.name = filename
        context.bot.send_document(chat_id=chat_id, document=document)

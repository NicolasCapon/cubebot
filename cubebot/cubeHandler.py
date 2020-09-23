import os
import logging
import config
import requests
import audio
import utils
from filters import restrict, UserType, SignConv, WinConv, GameStates
from model import session, Cube, CubeList, Game, Player, Card, Deck, DeckList
from deckHandler import DeckHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.ext import Filters, CommandHandler, ConversationHandler, MessageHandler, CallbackQueryHandler
from telegram.ext.dispatcher import run_async
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

class CubeHandler():

    def __init__(self, dispatcher):
        # Create handlers
        self.join_handler = CommandHandler("start", self.join)
        dispatcher.add_handler(self.join_handler)
        self.new_game_handler = CommandHandler("init", self.new_game)
        dispatcher.add_handler(self.new_game_handler)
        self.play_game_handler = CommandHandler("play", self.play_game)
        self.deckHandler = None
        self.win_handler = self.get_win_convHandler()
        self.sign_handler = self.get_sign_handler()
        # model
        self.cube, self.game = None, None
        
    @restrict(UserType.ADMIN)
    def new_game(self, update, context):
        # Remove entry point to only have one game at time
        context.dispatcher.remove_handler(self.new_game_handler)
        # Create SQL Object
        self.cube = session.query(Cube).first()
        # Update cube based on cubecobra
        update_count = utils.update_cube(self.cube)
        # Create new game
        self.game = Game(state=GameStates.INIT.name)
        self.cube.games.append(self.game)
        session.commit()
        logging.info("New game created")
        # Enable deck handlers
        self.deckHandler = DeckHandler(context.dispatcher, self.game)
        # Enable signature conv handlers
        context.dispatcher.add_handler(self.sign_handler)
        # Next state is now available for admin
        context.dispatcher.add_handler(self.play_game_handler)
        # Send ok message
        text = f"{update_count} mise(s) à jour trouvée(s) et appliquée(s).\n"\
                "Les joueurs peuvent commencer à scanner leur deck en tapant /deck."
        context.bot.send_message(chat_id=config.chat_id,
                                 text=text)
        
    @restrict(UserType.ADMIN)
    @run_async
    def play_game(self, update, context):
        self.game.state = GameStates.PLAY.name
        # Control if game has players ?
        # Remove entry point to only have one game at time
        if not self.deckHandler.stop_deck_preparation(context):
            text = f"Attention, {self.deckHandler.current_user.name} n'a pas encore fini de scanner"
            context.bot.send_message(chat_id=update.effective_chat.id,
                                     text=text)
            return False

        if context.args:
            # Add a specific game type instead of default Free for All
            self.game.type = " ".join(context.args)
        
        context.dispatcher.remove_handler(self.join_handler)
        context.dispatcher.remove_handler(self.new_game_handler)
        context.dispatcher.remove_handler(self.play_game_handler)
        context.dispatcher.add_handler(self.win_handler)
        
        logging.info("Game start")
        text = "La partie peut commencer !"
        context.bot.send_message(chat_id=config.chat_id, text=text)
        
        # Start nfc sanner
        audio.audio_scan(self.cube, context)

    def join(self, update, context):
        # first interaction with the bot
        user = update.message.from_user
        known_players_id = [id for id, in session.query(Player.id)]
        if not user.id in known_players_id and context.args and context.args[0] == config.password:
            new_player = Player(id=user.id, name=user.first_name)
            session.add(new_player)
            context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=f"Bienvenue {user.first_name}!")
            session.commit()
            logging.info(f"{new_player} has joined")
        else:
            return False

    def get_sign_handler(self):
        
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('sign', self.sign_card)],
            states={
                SignConv.CHOOSING: [MessageHandler(Filters.text, self.choose_card)],
                SignConv.CONFIRM: [CallbackQueryHandler(self.confirm_card)],
                SignConv.SENDING: [MessageHandler(Filters.audio | Filters.voice | Filters.entity(MessageEntity.URL), self.save_signature)]
                },
            fallbacks=[])

        return conv_handler

    @restrict(UserType.PLAYER)
    def sign_card(self, update, context):
        text = "Envoie moi le nom de la carte que tu souhaites signer. Attention, celle-ci doit faire partie de ton deck."
        update.message.reply_text(text)
        return SignConv.CHOOSING

    def choose_card(self, update, context):
        answer = update.message.text
        c = session.query(CubeList).join(Card).join(DeckList).join(Deck).filter(Deck.player_id==update.message.from_user.id,
                                                                 Deck.game_id==self.game.id,
                                                                 Card.name.like(f"{answer}%")
                                                                 ).first()
        avert = "Attention cette carte est déjà signée !\n"
        if c:
            text = f"{avert if c.signature  else ''}{c.card.name} - Est-ce bien ta carte ?"
            keyboard = [[InlineKeyboardButton("Annuler", callback_data='0'),
                         InlineKeyboardButton("Retenter", callback_data='2')],
                        [InlineKeyboardButton("Oui", callback_data='1')]]
            markup = InlineKeyboardMarkup(keyboard)
            context.user_data["sign_card_id"] = c.card.id
            update.message.reply_text(text=text,
                                      reply_markup=markup)
            return SignConv.CONFIRM

        else:
            update.message.reply_text("Je n'ai pas trouvé cette carte dans ton deck, recommence...")
            return SignConv.CHOOSING

    def confirm_card(self, update, context):
        query = update.callback_query
        if query.data == "1":
            text = "Ok, envoie moi un fichier audio pour signer ta carte."
            query.edit_message_text(text=text)
            return SignConv.SENDING

        elif query.data == "2":
            text = "No problemo, renvoie moi le nom de ta carte."
            query.edit_message_text(text=text)
            return SignConv.CHOOSING

        else:
            text = "Comme tu voudras."
            query.edit_message_text(text=text)
            return ConversationHandler.END
        
    def save_signature(self, update, context):
        card_id = context.user_data["sign_card_id"]
        if update.message.audio:
            file = context.bot.getFile(update.message.audio.file_id)
            filename, file_extension = os.path.splitext(file.file_path)
            dl_path = os.path.join(config.src_dir,"resources",
                                   "sounds",f"{self.cube.id}_{card_id}{file_extension}")
            file.download(dl_path)
            logging.info(f"audio downloaded ({dl_path})")

        elif update.message.voice:
            file = context.bot.getFile(update.message.voice.file_id)
            filename, file_extension = os.path.splitext(file.file_path)
            dl_path = os.path.join(config.src_dir,"resources",
                                   "sounds",f"{self.cube.id}_{card_id}{file_extension}")
            file.download(dl_path)
            logging.info(f"audio downloaded ({dl_path})")

        elif update.message.entities:
            url = update.message.parse_entity(update.message.entities[0])
            filename, file_extension = os.path.splitext(url)
            print(filename, file_extension)
            audio_formats = [".mp3", ".wav", ".ogg"]
            if file_extension in audio_formats: 
                r = requests.get(url)
                if r.ok:
                    dl_path = os.path.join(config.src_dir,"resources",
                                   "sounds",f"{self.cube.id}_{card_id}{file_extension}")
                    with open(dl_path, 'wb') as f:
                        f.write(r.content)
                        logging.info(f"audio downloaded ({dl_path})")
                else:
                    text = f"Le serveur répond avec un code d'erreur {r.status_code}. Essaye avec un autre lien."
                    update.message.reply_text(text=text)
                    return SignConv.SENDING
            else:
                text = f"Format audio inconnu. Essaye avec un autre lien avec l'un des formats suivant {audio_formats}."
                update.message.reply_text(text=text)
                return SignConv.SENDING

        else:
            text = "Format audio inconnu. Essayes-en un autre."
            update.message.reply_text(text=text)
            return SignConv.SENDING
        
        c = session.query(CubeList).filter(CubeList.card_id==card_id, CubeList.cube_id==self.cube.id).first()
        c.signature = f"{self.cube.id}_{card_id}{file_extension}"
        session.commit()
        logging.info(f"{c} signed with {c.signature}")
        text = "J'ai bien récupéré ton fichier audio. Ta carte est desormais signée."
        update.message.reply_text(text=text)
        return ConversationHandler.END
    
    def get_win_convHandler(self):
        
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('win', self.set_winner)],
            states={
                WinConv.CHOOSING: [CallbackQueryHandler(self.choose_winner)],
                WinConv.DESCRIPT: [MessageHandler(Filters.text, self.descript_game)]
                },
            fallbacks=[])

        return conv_handler

    @restrict(UserType.ADMIN)
    def set_winner(self, update, context):
        text = "Qui est le vainqueur de cette partie ?"
        keyboard = []
        for deck in self.game.decks:
            keyboard.append([InlineKeyboardButton(deck.player.name, callback_data=deck.player.id)])
        markup = InlineKeyboardMarkup(keyboard)
        context.user_data["winners"] = []
        update.message.reply_text(text=text,
                                  reply_markup=markup)
        return WinConv.CHOOSING

    def choose_winner(self, update, context):
        query = update.callback_query
        if query.data == "0":
            for deck in self.game.decks:
                if str(deck.player.id) in context.user_data["winners"]:
                    deck.is_winner = True
                    logging.info(f"{deck} set as winner")
            session.commit()
            text = "Ok ! Maintenant, envoie moi une description de la partie."
            query.edit_message_text(text=text)
            return WinConv.DESCRIPT

        elif query.data == "X":
            del context.user_data["winners"][-1]
            keyboard = []
            for deck in self.game.decks:
                if not str(deck.player_id) in context.user_data["winners"]:
                    keyboard.append([InlineKeyboardButton(deck.player.name, callback_data=deck.player_id)])
            if len(context.user_data["winners"]) > 0:
                keyboard.append([InlineKeyboardButton("Corriger", callback_data="X"),
                                 InlineKeyboardButton("Finir la partie", callback_data="0")])
            markup = InlineKeyboardMarkup(keyboard)
            text = "Qui est le vainqueur de cette partie ?\n"
            text += "\n".join(context.user_data["winners"])
            query.edit_message_text(text=text,
                                    reply_markup=markup)
            return WinConv.CHOOSING

        else:
            context.user_data["winners"].append(query.data)
            keyboard = []
            for deck in self.game.decks:
                if not str(deck.player_id) in context.user_data["winners"]:
                    keyboard.append([InlineKeyboardButton(deck.player.name, callback_data=deck.player_id)])
            keyboard.append([InlineKeyboardButton("Corriger", callback_data="X"),
                             InlineKeyboardButton("Finir la partie", callback_data="0")])
            markup = InlineKeyboardMarkup(keyboard)
            text = "Qui est le vainqueur de cette partie ?"
            winners = session.query(Player).filter(Player.id.in_(context.user_data["winners"])).all()
            for winner in winners:
                text += f"\n<a href='tg://user?id={winner.id}'>{winner.name}</a>"
            query.edit_message_text(text=text,
                                    reply_markup=markup,
                                    parse_mode="HTML")
            return WinConv.CHOOSING

    def descript_game(self, update, context):
        self.game.state = GameStates.END.name
        answer = update.message.text
        self.game.description = answer
        session.commit()
        logging.info("Game description saved")
        text = "Merci, la partie est bien enregistrée et terminée.\nPour en lancer une autre: /init"
        update.message.reply_text(text=text)
        # RESET all states to init
        context.job_queue.stop()
        context.dispatcher.remove_handler(self.win_handler)
        context.dispatcher.remove_handler(self.sign_handler)
        context.dispatcher.remove_handler(self.deckHandler.deck_conv_handler)
        context.dispatcher.add_handler(self.join_handler)
        context.dispatcher.add_handler(self.new_game_handler)
        logging.info("All states reset to INIT")
        return ConversationHandler.END

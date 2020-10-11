import os
import logging
import config
import requests
import audio
import utils
import deckstat_interface as deckstat
from nfc_scanner import NFC_Scanner
from time import sleep
from random import shuffle
from filters import restrict, UserType, SignConv, WinConv, GameStates, SealedConv
from model import session, Cube, CubeList, Game, Player, Card, Deck, DeckList
from deckHandler import DeckHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, ReplyKeyboardRemove
from telegram.ext import Filters, CommandHandler, ConversationHandler, MessageHandler, CallbackQueryHandler
from telegram.ext.dispatcher import run_async
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

class CubeHandler():

    def __init__(self, dispatcher):
        
        # Update cube based on cubecobra
        # update_count = utils.update_cube(self.cube)
        # context.bot.send_message(chat_id=config.chat_id,
                                 # text=f"{update_count} mise(s) à jour effectuée(s).")
        # Create handlers
        self.nfc_scan = NFC_Scanner()
        self.join_handler = CommandHandler("start", self.join)
        dispatcher.add_handler(self.join_handler)
        self.new_game_handler = CommandHandler("init", self.new_game)
        dispatcher.add_handler(self.new_game_handler)
        self.rematch_handler = CommandHandler("rematch", self.rematch)
        dispatcher.add_handler(self.rematch_handler)
        self.play_game_handler = CommandHandler("play", self.play_game)
        self.deckHandler = None
        self.win_handler = self.get_win_convHandler()
        self.sign_handler = self.get_sign_handler()
        dispatcher.add_handler(self.sign_handler)
        # model
        self.cube, self.game = None, None
        # Sealed
        self.players, self.sealed_players = [], []
        self.sealed_handler = self.get_sealed_convHandler()
        dispatcher.add_handler(self.sealed_handler)
        
    @restrict(UserType.ADMIN)
    def new_game(self, update, context):
        # Remove entry point to only have one game at time
        context.dispatcher.remove_handler(self.new_game_handler)
        context.dispatcher.remove_handler(self.sign_handler)
        context.dispatcher.remove_handler(self.rematch_handler)
        # Create SQL Object
        self.cube = session.query(Cube).first()
        # Update cube based on cubecobra
        # update_count = utils.update_cube(self.cube)
        # Create new game
        self.game = Game(state=GameStates.INIT.name)
        self.cube.games.append(self.game)
        session.commit()
        logging.info("New game created")
        # Enable deck handlers
        self.deckHandler = DeckHandler(context.dispatcher, self.game, self.nfc_scan)
        # Next state is now available for admin
        context.dispatcher.add_handler(self.play_game_handler)
        # Send ok message
        text = "Les joueurs peuvent commencer à scanner leur deck en tapant /scan."
        context.bot.send_message(chat_id=config.chat_id,
                                 text=text)
    
    @restrict(UserType.ADMIN)
    def rematch(self, update, context):
        # Remove entry point to only have one game at time
        context.dispatcher.remove_handler(self.new_game_handler)
        context.dispatcher.remove_handler(self.sign_handler)
        context.dispatcher.remove_handler(self.rematch_handler)
        # Create SQL Object
        self.cube = session.query(Cube).first()
        # Update cube based on cubecobra
        # update_count = utils.update_cube(self.cube)
        # Create new game
        last_game = session.query(Game).order_by(Game.id.desc()).first()
        self.game = Game(state=GameStates.INIT.name)
        for deck in last_game.decks:
            d = Deck(player=deck.player, name=deck.name, description=deck.description)
            for deck_card in deck.cards:
                DeckList(deck=d, card=deck_card.card, amount=deck_card.amount, note=deck_card.note)
            self.game.decks.append(d)
        
        self.cube.games.append(self.game)
        session.commit()
        logging.info(f"New game created from game [{last_game}]")
        # Enable deck handlers
        self.deckHandler = DeckHandler(context.dispatcher, self.game, self.nfc_scan)
        # Next state is now available for admin
        context.dispatcher.add_handler(self.play_game_handler)
        # Send ok message
        text = "Les joueurs peuvent visualiser et éditer leur ancien deck avec la commande: /mydeck."
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
        self.nfc_scan.start(self.game_scanner, context)

    def game_scanner(self, uid, context):
        cubelist, decklist = None, None
        result = session.query(CubeList, DeckList).join(Deck).filter(CubeList.card_id == DeckList.card_id, Deck.game_id == self.game.id).filter(CubeList.cube_id == self.cube.id,
                             CubeList.uid == uid).first()
        print(result)
        if result is not None:
            cubelist, decklist = result
            if decklist.note:
                context.bot.send_message(chat_id=config.chat_id,
                                         text=decklist.note)
                if not cubelist.signature:
                    sleep(3)
            if cubelist.signature:
                s = os.path.join(config.src_dir, "resources", "sounds", cubelist.signature)
                audio.play_sound(s)

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
                SignConv.CHOOSING: [MessageHandler(Filters.text & (~ Filters.command), self.choose_card)],
                SignConv.CONFIRM: [CallbackQueryHandler(self.confirm_card)],
                SignConv.SENDING: [MessageHandler(Filters.audio | Filters.voice | Filters.entity(MessageEntity.URL), self.save_signature)]
                },
            fallbacks=[CommandHandler('stop', self.stop)])

        return conv_handler

    def stop(self, update, context):
        text = "Pour signer de nouveau une carte: /sign"
        update.message.reply_text(text=text, reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    @restrict(UserType.PLAYER)
    def sign_card(self, update, context):
        self.cube = session.query(Cube).first()
        text = "Envoie moi le nom de la carte que tu souhaites signer.\n(/stop pour quitter)"
        update.message.reply_text(text)
        return SignConv.CHOOSING

    def choose_card(self, update, context):
        answer = update.message.text
        c = session.query(CubeList).join(Card).filter(Card.name.like(f"{answer}%")).first()
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
            return SignConv.CONFIRM

        else:
            update.message.reply_text("Je n'ai pas trouvé cette carte, recommence.\n(/stop pour quitter)")
            return SignConv.CHOOSING

    def confirm_card(self, update, context):
        query = update.callback_query
        if query.data == "1":
            text = "Ok, envoie moi un fichier audio pour signer ta carte.\n(/stop pour quitter)"
            query.edit_message_text(text=text)
            return SignConv.SENDING

        elif query.data == "2":
            text = "No problemo, renvoie moi le nom de ta carte.\n(/stop pour quitter)"
            query.edit_message_text(text=text)
            return SignConv.CHOOSING

        else:
            text = "Comme tu voudras.\nPour signer de nouveau une carte: /sign"
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
            logging.info(f"Audio to download from url: {url}")
            audio_formats = [".mp3", ".m4a", ".ogg"]
            if file_extension in audio_formats: 
                r = requests.get(url)
                if r.ok:
                    dl_path = os.path.join(config.src_dir,"resources",
                                   "sounds",f"{self.cube.id}_{card_id}{file_extension}")
                    with open(dl_path, 'wb') as f:
                        f.write(r.content)
                        logging.info(f"audio downloaded ({dl_path})")
                else:
                    text = f"Le serveur répond avec un code d'erreur {r.status_code}. Essaye avec un autre lien.\n(/stop pour quitter)"
                    update.message.reply_text(text=text)
                    return SignConv.SENDING
            else:
                text = f"Format audio inconnu. Essaye avec un autre lien avec l'un des formats suivant {audio_formats}.\n(/stop pour quitter)"
                update.message.reply_text(text=text)
                return SignConv.SENDING

        else:
            text = "Format audio inconnu. Essayes-en un autre.\n(/stop pour quitter)"
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
            winners = session.query(Player).filter(Player.id.in_(context.user_data["winners"])).all()
            for winner in winners:
                text += f"\n<a href='tg://user?id={winner.id}'>{winner.name}</a>"
            query.edit_message_text(text=text,
                                    reply_markup=markup,
                                    parse_mode="HTML")
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
        text = "Merci, la partie est bien enregistrée et terminée."\
               "\nPour en lancer une autre: /init"\
               "\nPour recommencer avec ces decks: /rematch"
        update.message.reply_text(text=text)
        # RESET all states to init
        self.nfc_scan.stop()
        context.job_queue.stop()
        context.dispatcher.remove_handler(self.win_handler)
        context.dispatcher.remove_handler(self.sign_handler)
        context.dispatcher.remove_handler(self.deckHandler.deck_conv_handler)
        context.dispatcher.add_handler(self.join_handler)
        context.dispatcher.add_handler(self.new_game_handler)
        context.dispatcher.add_handler(self.sign_handler)
        context.dispatcher.add_handler(self.sealed_handler)
        context.dispatcher.add_handler(self.rematch_handler)
        logging.info("All states reset to INIT")
        return ConversationHandler.END

    def get_sealed_convHandler(self):
        
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('sealed', self.start_sealed)],
            states={
                SealedConv.CHOOSING: [CallbackQueryHandler(self.choose_players_sealed)]
                },
            fallbacks=[])

        return conv_handler
    
    def get_sealed_keyboard(self):
        keyboard = []
        if len(self.sealed_players) < 4:
            for player in self.players:
                if player not in self.sealed_players:
                    keyboard.append([InlineKeyboardButton(player.name, callback_data=player.id)])
        if len(self.sealed_players):
            keyboard.append([InlineKeyboardButton("Corriger", callback_data="-1"),
                             InlineKeyboardButton("Envoyer", callback_data="1")])
        keyboard.append([InlineKeyboardButton("Annuler", callback_data="0")])
        return keyboard
            
    @restrict(UserType.ADMIN)
    def start_sealed(self, update, context):
        #CallbackQueryHandler(self.scan_buttons)
        self.players = session.query(Player).all()
        reply_markup = InlineKeyboardMarkup(self.get_sealed_keyboard())
        text = "Selectionne les joueurs à qui envoyer un scellé."
        message = update.message.reply_text(text=text,
                                            reply_markup=reply_markup)
        return SealedConv.CHOOSING
        
    def choose_players_sealed(self, update, context):
        query = update.callback_query
        
        if query.data == "0":
            text = "Scellé annulé, pour recommencer: /sealed"
            query.edit_message_text(text=text)
            return ConversationHandler.END
        
        elif query.data == "1":
            # Send
            cards = session.query(Card).join(CubeList).join(Cube).filter(Cube.id == 1, Card.type_line != "Basic Land").all()
            shuffle(cards)
            sealed_size = 90
            start = 0
            final_text = "Les scellés ont bien été envoyés à :\n"
            for player in self.sealed_players:
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
            
            query.edit_message_text(text=final_text)
            return ConversationHandler.END
        
        elif query.data == "-1":
            # Remove last
            del self.sealed_players[-1]
            text = "Joueurs selectionnés:\n"
            for player in self.sealed_players:
                text += f"- {player.name}\n"
        
        else:
            # Add player
            player = session.query(Player).filter(Player.id == int(query.data)).first()
            self.sealed_players.append(player)
            text = "Joueurs selectionnés:\n"
            for player in self.sealed_players:
                text += f"- {player.name}\n"
        
        reply_markup = InlineKeyboardMarkup(self.get_sealed_keyboard())
        query.edit_message_text(text=text,
                                parse_mode="HTML",
                                reply_markup=reply_markup)
        return SealedConv.CHOOSING

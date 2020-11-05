import config
import logging
from datetime import datetime
from deckstat_interface import load_deck
from sqlalchemy import Column, Integer, String, Binary, Boolean, DateTime, create_engine
from sqlalchemy.orm import sessionmaker, relationship, backref
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.hybrid import hybrid_method, hybrid_property
from sqlalchemy.sql.schema import ForeignKey
from collections import deque
from random import shuffle
engine = create_engine(config.db, connect_args={'check_same_thread': False})
Base = declarative_base()

class Card(Base):

    __tablename__ = 'card'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    set_code = Column(String)
    cmc = Column(Integer)
    color = Column(String)
    type_line = Column(String)
    status = Column(String)
    tags = Column(String)
    scryfall_id = Column(String)
    image_url = Column(String)
    
    cubes = relationship("Cube", secondary='cubelist', back_populates="cards")
    tokens = relationship("Token", secondary='tokenlist', back_populates="cards")

    def __repr__(self):
        return f"<Card(id={self.id}, name={self.name},  set_code={self.set_code}, "\
               f"cmc={self.cmc}, color={self.color}, type_line={self.type_line}, "\
               f"status={self.status}, tags={self.tags}, scryfall_id={self.scryfall_id}, "\
               f"image_url={self.image_url})>"

class Token(Base):
    
    __tablename__ = 'token'
    
    id = Column(Integer, primary_key=True)
    name = Column(String)
    power = Column(Integer)
    toughness = Column(Integer)
    color = Column(String)
    scryfall_id = Column(String)
    image_url = Column(String)
    
    cards = relationship("Card", secondary='tokenlist')
    
    def __repr__(self):
        return f"<Token(id={self.id}, name={self.name}, power={self.power}, "\
               f"toughness={self.toughness}, color={self.color}, "\
               f"scryfall_id={self.scryfall_id}, image_url={self.image_url})>"
    
class TokenList(Base):
    
    __tablename__ = "tokenlist"
    
    card_id = Column(Integer, ForeignKey("card.id"), primary_key=True)
    token_id = Column(Integer, ForeignKey("token.id"), primary_key=True)
    
    card = relationship(Card)
    token = relationship(Token)
    
    def __repr__(self):
        return f"<TokenList(card_id={self.card_id}, token_id={self.token_id})> "
    
class Cube(Base):

    __tablename__ = 'cube'

    id = Column(Integer, primary_key=True)
    name = Column(String)
    cubecobra_id = Column(String)
    last_update = Column(DateTime, default=datetime.now())

    games = relationship("Game")
    cards = relationship("Card", secondary='cubelist')

    def __repr__(self):
        return f"<Cube(id={self.id}, name={self.name}, "\
               f"cubecobra_id={self.cubecobra_id})>"


class CubeList(Base):

    __tablename__ = "cubelist"

    cube_id = Column(Integer, ForeignKey("cube.id"), primary_key=True)
    card_id = Column(Integer, ForeignKey("card.id"), primary_key=True)
    signature = Column(String)
    uid = Column(Binary)

    cube = relationship(Cube)
    card = relationship(Card)

    def __repr__(self):
        return f"<CubeList(cube_id={self.cube_id}, card_id={self.card_id}, "\
               f"signature={self.signature}, uid={self.uid})>"

    
class Game(Base):
     
    __tablename__ = 'game'

    id = Column(Integer, primary_key=True)
    date = Column(DateTime, default=datetime.now())
    mode = Column(String, default="Free for All")
    description = Column(String)
    state = Column(String)

    decks = relationship("Deck")

    cube_id = Column(Integer, ForeignKey("cube.id"))
    cube = relationship("Cube", back_populates="games")
    
    @hybrid_method
    def get_deck_from_player_id(self, player_id):
        for deck in self.decks:
            if deck.player.id == player_id:
                return deck
        return None

    @hybrid_property
    def duration(self):
        return datetime.now() - self.date
        
    def __repr__(self):
        return f"<Game(id={self.id}, date={self.date}, "\
               f"mode={self.mode}, description={self.description}, state={self.state}, "\
               f"cube_id={self.cube_id})>"


class Player(Base):
     
    __tablename__ = 'player'

    id = Column(Integer, primary_key=True)
    name = Column(String)
    is_admin = Column(Boolean, default=False)
    
    decks = relationship("Deck")
        
    def __repr__(self):
        return f"<Player(id={self.id}, name={self.name}, "\
               f"is_admin={self.is_admin})>"


class Deck(Base):
     
    __tablename__ = 'deck'

    id = Column(Integer, primary_key=True)
    is_winner = Column(Boolean, default=False)
    name = Column(String, default = "Mon Deck")
    description = Column(String)

    player_id = Column(Integer, ForeignKey("player.id"))
    player = relationship("Player", back_populates="decks")

    game_id = Column(Integer, ForeignKey("game.id"))
    game = relationship("Game", back_populates="decks")

    cards = association_proxy('cards', 'decklist')# relationship("DeckList")
    
    deckstats = None
    
    def set_is_winner(self, is_winner):
        self.is_winner = is_winner

    @hybrid_property
    def card_count(self):
        count = 0
        for deck_card in self.cards:
            count += deck_card.amount
        return count
    
    @hybrid_method
    def add_card(self, card, amount=1, note=None):
        for deck_card in self.cards:
            if card.id == deck_card.card_id:
                deck_card.amount += amount
                return True
        
        self.cards.append(DeckList(deck=self, card=card, amount=amount, note=note))
        return True
    
    @hybrid_method
    def remove_card(self, card, amount=1):
        for deck_card in self.cards:
            if card.id == deck_card.card_id:
                if amount >= deck_card.amount:
                    # If we want to remove more than existing, remove card
                    self.cards.remove(deck_card)
                else:
                    deck_card.amount -= amount
                return True
        return False

    @hybrid_method
    def load_deckstats_data(self, url):
        deckstats_deck = load_deck(url)
        if not deckstats_deck: return False
        errors = []
        for card in deckstats_deck["cards"]:
            db_card = session.query(Card).join(CubeList).filter(Card.name == card.get("name", ""),
                                                                CubeList.cube_id == self.game.cube_id).first()
            if db_card:
                self.add_card(db_card, amount=card.get("amount",1), note=card.get("comment",None))
            else:
                errors.append(card.get("name", ""))
        self.name = deckstats_deck["title"]
        self.description = deckstats_deck["description"]
        return errors

    def __repr__(self):
        return f"<Deck(id={self.id}, card_count:{self.card_count}, is_winner={self.is_winner}, "\
               f"game_id={self.game_id}, player_id={self.player_id}, "\
               f"name={self.name}, description={self.description})>"


class DeckList(Base):

    __tablename__ = "decklist"

    deck_id = Column(Integer, ForeignKey("deck.id"), primary_key=True)
    card_id = Column(Integer, ForeignKey("card.id"), primary_key=True)
    amount = Column(Integer, default=1)
    note = Column(String)
    
    # is_sideboard = Column(Boolean, default=False)

    deck = relationship(Deck,
                backref=backref("cards",
                                cascade="all, delete-orphan")
            )#relationship(Deck)
    card = relationship(Card)

    def __init__(self, deck=None, card=None, amount=None, note=None):
        self.deck = deck
        self.card = card
        self.amount = amount
        self.note = note
        
    def __repr__(self):
        return f"<DeckList(deck_id={self.deck_id}, card_id={self.card_id}, "\
               f"amount={self.amount}, note={self.note})>"


class Draft(Base):

    __tablename__ = "draft"
    
    id = Column(Integer, primary_key=True)
    date = Column(DateTime, default=datetime.now())
    booster_size = Column(Integer)
    round_num = Column(Integer)
    state = Column(String)

    cube_id = Column(Integer, ForeignKey("cube.id"))
    cube = relationship("Cube")

    partners = [{"name":"Krav, the Unredeemed", "partner":"Regna, the Redeemer"},
                {"name": "Virtus the Veiled", "partner": "Gorm the Great"},
                {"name": "Brallin, Skyshark Rider", "partner": "Shabraz, the Skyshark"}]

    def __init__(self, cube, round_num=6, booster_size=9, auto_pick_last_card=True):
        self.cube = cube
        self.boosters = []
        self.booster_size = booster_size
        self.round_num = round_num
        self.state = "INIT"
        self.turn_order = -1
        self.drafters = []
        self.round = None
        self.round_count = 0
        self.auto_pick_last_card = auto_pick_last_card

    @hybrid_method
    def add_drafter(self, drafter):
        drafter.draft = self
        self.drafters.append(drafter)
        return self.drafters

    @hybrid_method
    def start(self):
        if not self.boosters:
            logging.info("No boosters loaded")
            return False
        logging.info("DRAFT STARTS")
        session.add(self)
        self.round = self.get_round()
        self.state = "PLAY"
        session.commit()
        logging.info(self)
        return True

    @hybrid_method    
    def get_round(self):
        if self.round_count < self.round_num:
            logging.info(">>>>>>>>>>>>>>>>> NEW ROUND >>>>>>>>>>>>>>>>>")
            self.turn_order = -self.turn_order
            self.round_count += 1
            r = []
            for drafter in self.drafters:
                r.append(self.boosters.pop())
                drafter.reset_pick_count()
            return deque(r)
        else:
            logging.info("DRAFT ENDS")
            self.state = "END"
            session.commit()
            return []

    @hybrid_method
    def get_drafter_by_id(self, id):
        for drafter in self.drafters:
            if drafter.id == id:
                return drafter

    @hybrid_method    
    def get_booster(self, drafter):
        i = self.drafters.index(drafter)
        return self.round[i] if i < len(self.round) else None

    @hybrid_method
    def control_choices(self):
        if all(drafter.choice for drafter in self.drafters):
            for drafter in self.drafters:
                drafter.pick()
            is_new_booster, is_new_round = self.rotate_boosters()
            # Auto pick last card
            if self.auto_pick_last_card:
                for drafter in self.drafters:
                    booster = drafter.get_booster()
                    if booster and len(booster.cards) == 1:
                        is_new_booster, is_new_round = drafter.choose(booster.cards[0])
            return is_new_booster, is_new_round
        else:
            return False, False

    @hybrid_method
    def rotate_boosters(self):
        is_new_booster = False
        is_new_round = False
        if any(booster.cards for booster in self.round):
            # If a booster still has cards, pass it to the next player
            logging.info("================== ROTATE ==================")
            self.round.rotate(self.turn_order)
            is_new_booster = True
        else:
            # Create new round
            self.round = self.get_round()
            is_new_booster = True
            is_new_round = True
        
        return is_new_booster, is_new_round

    @hybrid_method
    def add_booster(self, drafter):
        # Tips, add booster before adding choice
        i = self.drafters.index(drafter)
        self.drafters.insert(i, 0)
        self.round.insert(i, self.boosters.pop())
        
    def __repr__(self):
        return f"<Draft(id={self.id}, turn_order={self.turn_order})>"

     
class Drafter():
        
    draft = None
    
    def __init__(self, id, name, data= None, pool=[]):
        self.id = id
        self.name = name
        self.choice = None
        self.data = None
        self.pool = []
        self.pick_count = self.reset_pick_count()

    def reset_pick_count(self):
        self.pick_count = 1
        
    def choose(self, card):
        if card in self.get_booster().cards:
            self.choice = Choice(self, card, self.draft.round_count, self.pick_count)
            return self.draft.control_choices()
        
    def pick(self):
        booster = self.get_booster()
        if self.choice and booster:
            logging.info(self.choice)
            self.choice.booster_id = booster.id
            session.add(self.choice)
            booster.from_drafter = self
            session.commit()
            booster.remove_card(self.choice.card)
            # Control if partner with TODO : Wrapper
            for partner in self.draft.partners:
                if self.choice.card.name == partner["name"]:
                    self.pool.append(session.query(Card).filter(Card.name == partner["partner"]).first())
            self.pool.append(self.choice.card)
            self.choice = None
            self.pick_count += 1
            return True
    
    def get_booster(self):
        i = self.draft.drafters.index(self)
        return self.draft.round[i] if i < len(self.draft.round) else None
    
    def __repr__(self):
        return f"<Drafter(id={self.id}, name={self.name})>"


class Choice(Base):

    __tablename__ = "choice"

    draft_id = Column(Integer, ForeignKey("draft.id"), primary_key=True)
    draft = relationship("Draft")
    
    card_id = Column(Integer, ForeignKey("card.id"))
    card = relationship("Card")

    drafter_id = Column(Integer, primary_key=True)
    round_count = Column(Integer, primary_key=True)
    pick_count = Column(Integer, primary_key=True)
    booster_id = Column(Integer)
    
    
    def __init__(self, drafter, card, round_count, pick_count):
        self.drafter = drafter
        self.drafter_id = self.drafter.id
        self.draft = drafter.draft
        self.card = card
        self.round_count = round_count
        self.pick_count = pick_count
        
    def __repr__(self):
        return f"<Choice(drafter={self.drafter}, card={self.card}, round_count={self.round_count}, "\
               f"pick_count={self.pick_count})>"
            
            
class Booster():
    
    def __init__(self, id, cards, from_drafter=None):
        self.id = id
        self.cards = cards
        self.from_drafter = from_drafter

    def remove_card(self, card):
        if card in self.cards:
            self.cards.remove(card)
            return card

    def __repr__(self):
        return f"<Booster(id={self.id}, size={len(self.cards)}, from_drafter={self.from_drafter})>"
        
    
Base.metadata.create_all(engine)
DBSession = sessionmaker(bind=engine)
session = DBSession()

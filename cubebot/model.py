import config
from datetime import datetime
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

    def __repr__(self):
        return f"<Deck(id={self.id}, is_winner={self.is_winner}, "\
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

class Draft():
    
    id = 1
    turn_order = 1
    boosters = []
    drafters = []
    choices = []
    round_num=0
    booster_size=0

    def __init__(self, cube, round_num=5, booster_size=9):
        cube_cards = cube.cards
        shuffle(cube_cards)
        self.boosters = [Booster(cube_cards[i:i+booster_size]) for i in range(0, len(cube_cards), booster_size)]
        self.round = None
        self.round_num = round_num

    def add_drafter(self, drafter):
        self.drafters.append(drafter)
        return self.drafters
    
    def start(self):
        self.round = self.get_round()
        
    def get_round(self):
        return deque([self.boosters.pop() for d in self.drafters])
    
    def get_drafter_by_id(self, id):
        for drafter in drafters:
            if drafter.id == id:
                return drafter
    
    def pick(self, choice):
        booster = self.round[self.drafters.index(choice.drafter)]
        card = booster.cards.pop(booster.cards.index(choice.card))
        choice.drafter.pool.append(card)
        return booster
        
    def get_booster(self, drafter):
        return self.round[self.drafters.index(drafter)]
    
    def add_choice(self, c):
        for choice in self.choices:
            if choice.drafter == c.drafter:
                choice.card = c.card
                return 1
        self.choices.append(c)
        if len(self.choices) == len(self.drafters) or len(self.choices) == len(self.round):
            [self.pick(choice) for choice in self.choices]
            self.rotate()
            return -1
    
    def rotate(self):
        for booster in self.round:
            if booster:
                self.round.rotate(self.turn_order)
                self.choices = []
                return True
        # End of round
        self.turn_order = -self.turn_order
        # TODO Remove any phantom player (additionnal boosters)
        
        # Create new round
        self.round_num -= 1
        if self.round_num:
            self.round = self.get_round()
            return False
        # End of draft    
        else: return -1
    
    def add_booster(self, drafter):
        # Tips, add booster before adding choice
        i = self.drafters.index(drafter)
        self.drafters.insert(i, 0)
        self.round.insert(i, self.boosters.pop())
        
    def __repr__(self):
        return f"<Draft(id={self.id}, turn_order={self.turn_order})>"
            
class Drafter():
        
    id = None
    pool = []
    draft = None
    
    def __init__(self, id, draft, pool=[]):
        self.id = id
        self.draft = draft
        self.pool = pool
        
    def choose(self, card):
        return self.draft.add_choice(Choice(self, card))
        
    def __repr__(self):
        return f"<Drafter(id={self.id})>"
        
class Choice():
    
    def __init__(self, drafter, card):
        self.drafter = drafter
        self.card = card
        
    def __repr__(self):
        return f"<Choice(drafter={self.drafter}, card={self.card})>"
            
            
class Booster():
    
    id = 1
    cards = []
    
    def __init__(self, cards, id=1):
        self.id = id
        self.cards = cards
        
    def __repr__(self):
        return f"<Booster(id={self.id})>"
        
    
Base.metadata.create_all(engine)
DBSession = sessionmaker(bind=engine)
session = DBSession()

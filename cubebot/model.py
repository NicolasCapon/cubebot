import config
from datetime import datetime
from sqlalchemy import Column, Integer, String, Binary, Boolean, DateTime, create_engine
from sqlalchemy.orm import sessionmaker, relationship, backref
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql.schema import ForeignKey

engine = create_engine(config.db, connect_args={'check_same_thread': False})
Base = declarative_base()

class Card(Base):

    __tablename__ = 'card'

    id = Column(Integer, primary_key=True)
    name = Column(String)
    set_code = Column(String)
    cmc = Column(Integer)
    color = Column(String) # Color in cube
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

    cube = relationship(Cube)#, backref=backref("cube_assoc"))
    card = relationship(Card)#, backref=backref("card_assoc"))

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

    cards = relationship("Card", secondary="decklist")

    def set_is_winner(self, is_winner):
        self.is_winner = is_winner

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

    deck = relationship(Deck)
    card = relationship(Card)

    def __init__(self, deck=None, card=None, amount=None):
        self.deck = deck
        self.card = card
        self.amount = amount

    def __repr__(self):
        return f"<CubeList(deck_id={self.deck_id}, card_id={self.card_id}, "\
               f"amount={self.amount}, note={self.note})>"

Base.metadata.create_all(engine)
DBSession = sessionmaker(bind=engine)
session = DBSession()

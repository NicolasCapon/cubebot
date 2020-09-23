import re
import requests
import logging

def get_deck_url(deck):
    """Get deck url on deckstat.net from given deck.
    Code inspired from cockatrice:
    https://github.com/Cockatrice/Cockatrice/blob/master/cockatrice/src/deckstats_interface.cpp"""
    if not deck.cards:
        return None
    #Prepare request
    url = 'https://deckstats.net/index.php'
    headers = {"Content-type":"application/x-www-form-urlencoded"}
    decklist = ""
    for i, deck_card in enumerate(deck.cards):
        br = "\n"
        decklist += f"{deck_card.amount}x {deck_card.card.name}{br if i<len(deck.cards)-1 else ''}"
    data = {"deck": decklist, "decktitle":deck.name}
    # Request and handle error status
    r = requests.post(url, data=data, headers=headers)
    if r.ok:
        # Regex to find deck url in page content
        regex = "<meta property=\"og:url\" content=\"([^\"]+)\""
        m = re.findall(regex, r.text)
        if m:
            return m[0]
        else:
            logging.info("Match not found in page content for deck:\n{0}\n{1}.".format(deck_name, deck))
            return None
    else:
        logging.info("Deckstat request failed {0}.".format(r))
        return None


if __name__ == "__main__":
    from model import Card, Deck
    c1 = Card(name="Snap")
    c2 = Card(name="Tropical Island")
    deck = Deck(name="test")
    deck.cards.append(c1)
    deck.cards.append(c2)
    print(get_deck_url(deck))

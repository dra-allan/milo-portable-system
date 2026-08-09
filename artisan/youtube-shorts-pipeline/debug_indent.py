import tokenize
import sys

with open('src/main.py', 'rb') as f:
    content = f.read()

try:
    tokens = list(tokenize.tokenize(iter(content.splitlines(True)).__next__))
    for tok in tokens:
        if tok.start[0] >= 650 and tok.start[0] <= 660:
            print('{}:{} {} {}'.format(tok.start[0], tok.start[1], tok.type, repr(tok.string)))
except tokenize.TokenError as e:
    print('TokenError:', e)
except IndentationError as e:
    print('IndentationError:', e)
except Exception as e:
    print('Error:', type(e).__name__, e)
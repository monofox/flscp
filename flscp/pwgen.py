#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: fenc=utf-8:ts=8:sw=8:si:sta:noet
## {{{ http://code.activestate.com/recipes/578169/ (r6)
from os import urandom
from random import choice

char_set = {'small': 'abcdefghijklmnopqrstuvwxyz',
             'nums': '0123456789',
             'big': 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
             'special': '^!$%&()=?{[]}+#-_.:;<>'
            }


def generate_pass(length=21):
    """Function to generate a password"""

    password = []

    while len(password) < length:
        key = choice(list(char_set.keys()))
        try:
            a_char = urandom(1).decode('utf-8')
        except:
            continue

        if a_char in char_set[key]:
            if check_prev_char(password, char_set[key]):
                continue
            else:
                password.append(a_char)
    return ''.join(password)


def check_prev_char(password, current_char_set):
    """Function to ensure that there are no consecutive 
    UPPERCASE/lowercase/numbers/special-characters."""

    index = len(password)
    if index == 0:
        return False
    else:
        prev_char = password[index - 1]
        if prev_char in current_char_set:
            return True
        else:
            return False

## end of http://code.activestate.com/recipes/578169/ }}}

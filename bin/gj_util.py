#!/usr/bin/env python
# -*- encoding: utf8 -*-

import os
import platform
import re
import subprocess
import sys


__author__ = 'fcamel'

#------------------------------------------------------------------------------
# Configuration
#------------------------------------------------------------------------------

LANG_MAP_FILE     = "id-lang.map"

# Input mappings
A_KEEP_STATEMENT  = ';'
A_CLEAN_STATEMENT = '!;'
A_FOLD            = '.'
A_RESTART         = '~'

#-----------------------------------------------------------
# public
#-----------------------------------------------------------
class Match(object):
    def __init__(self, tokens, pattern):
        self.filename, self.line_num, self.text = tokens
        self.line_num = int(self.line_num)
        self.column = self.text.index(pattern)

    @staticmethod
    def create(line, pattern):
        tokens = line.split(':', 2)
        if len(tokens) != 3:
            return None
        return Match(tokens, pattern)

    def __unicode__(self):
        tokens = [self.filename, self.line_num, self.column, self.text]
        return u':'.join(map(unicode, tokens))

    def __str__(self):
        return str(unicode(self))

    def __cmp__(self, other):
        try:
            cmp
        except NameError:
            cmp = lambda a, b: (a > b) - (a < b)
        r = cmp(self.filename, other.filename)
        if r:
            return r
        return cmp(self.line_num, other.line_num)

    def __lt__(self, other):
        if self.filename == other.filename:
            return self.line_num < other.line_num
        return self.filename < other.filename

def check_install():
    for cmd in ['mkid', _get_gid_cmd()]:
        if not _is_cmd_exists(cmd):
            msg = (
                "The program '%s' is currently not installed.  "
                "You can install it by typing:\n" % cmd
            )
            install_cmd = _get_idutils_install_cmd()
            if install_cmd:
                msg += install_cmd
            else:
                msg += "  (Unknown package manager. Try to install id-utils anyway.)\n"
                msg += "  (http://www.gnu.org/software/idutils/)"
            print(msg)
            sys.exit(1)

def build_index():
    path = os.path.join(os.path.dirname(__file__), LANG_MAP_FILE)
    return _mkid(path)


def _find_matches(pattern):
    lines = _gid(pattern)
    # gid may get unmatched pattern when the argument is a number.
    # Don't know the reason. Manually filter unmatched lines.
    candidated_lines = []
    for line in lines:
        tokens = line.split(':', 2)
        if len(tokens) == 3 and pattern in tokens[2]:
            candidated_lines.append(line)
    matches = [Match.create(line, pattern) for line in candidated_lines]
    return [m for m in matches if m]


def find_matches(patterns=None, filter_='', path_prefix=''):
    if patterns is None:
        patterns = find_matches.original_patterns
    matches = _find_matches(patterns[0])
    for pattern in patterns[1:]:
        matches = _filter_pattern(matches, pattern)

    if path_prefix:
        matches = _filter_filename(matches, '^' + path_prefix, False)

    if filter_:
        matches_by_filter = _find_matches(filter_)
        filenames = set(m.filename for m in matches_by_filter)
        matches = [m for m in matches if m.filename in filenames]

    return sorted(matches)

find_matches.original_patterns = []

def filter_until_select(matches, patterns, last_n):
    '''
    Return:
        >0: selected number.
         0: normal exit.
        <0: error.
    '''
    matches = matches[:]  # Make a clone.

    # Enter interactive mode.
    if not hasattr(filter_until_select, 'fold'):
        filter_until_select.fold = False
    while True:
        if not matches:
            print('No file matched.')
            return [], matches, patterns

        matches = sorted(set(matches))
        _show_list(matches, patterns, last_n, filter_until_select.fold)
        global input
        try:
            input = raw_input
        except NameError:
            pass
        response = input(_get_prompt_help()).strip()
        if not response:
            return [], matches, patterns

        if re.match('\d+', response):
            break

        # Clean/Keep statements
        if response in [A_CLEAN_STATEMENT, A_KEEP_STATEMENT]:
            matches = _filter_statement(matches, response == A_CLEAN_STATEMENT)
            continue

        if response == A_FOLD:
            filter_until_select.fold = not filter_until_select.fold
            continue

        if response[0] == A_RESTART:
            if len(response) == 1:
                matches = find_matches()
            else:
                patterns = response[1:].split()
                matches = find_matches(patterns)
            continue

        # Clean/Keep based on filename
        if response[0] == '!':
            exclude = True
            response = response[1:]
        else:
            exclude = False
        matches = _filter_filename(matches, response, exclude)

    matches.sort()

    # Parse the selected number
    ns = parse_number(response)
    if not ns:
        print('Invalid input.')
        return None, matches, patterns

    for n in ns:
        if n < 1 or n > len(matches):
            print('Invalid input.')
            return None, matches, patterns

    return ns, matches, patterns

def find_declaration_or_definition(pattern, level):
    if level <= 0:
        return []

    # Level 1 Rules:
    if pattern.startswith('m_') or pattern.startswith('s_'):
        # For non-static member fields or static member fields,
        # find symobls in header files.
        matches = find_matches([pattern])
        return _filter_filename(matches, '\.h$', False)

    matches = tuple(find_matches([pattern]))
    # Find declaration if possible.
    result = set()
    types = (
        'class',
        'struct',
        'enum',
        'interface',  # Java, Objective C
    )
    for type_ in types:
        tmp = _filter_pattern(matches, type_)
        tmp = _filter_statement(tmp, True)
        result.update(tmp)
    result.update(_filter_pattern(matches, 'typedef'))
    result.update(_filter_pattern(matches, 'define'))
    # Find definition if possible.
    result.update(_keep_possible_definition(matches, pattern))

    # Level 2 Rules:
    if level > 1:
        # Treat pattern as file name to filter results.
        old_result = result
        result = set()
        for filename in _find_possible_filename(pattern):
            result.update(_filter_filename(old_result, filename, False))

    return sorted(result)

def find_symbols(pattern, verbose=False, path_pattern=''):
    if path_pattern:
        verbose = True

    args = ['-lis']
    if not verbose:
        args.extend(('-R', 'none'))
    lines = _lid(pattern, args)
    result = []
    max_width = 120
    indent = 8
    for line in lines:
        tokens = line.split()
        if path_pattern:
            paths = tokens[1:]
            matched = False
            for p in paths:
                if path_pattern in p:
                    matched = True
                    break
            if not matched:
                continue

        if len(line) < max_width:
            result.append(line)
            continue

        first_line = True
        current_length = 0
        ts = []
        for i, tk in enumerate(tokens):
            if i and path_pattern and path_pattern not in tk:
                # Filter non-matched file paths
                continue
            length = len(tk)
            if current_length + length > max_width:
                prefix = '' if first_line else ' ' * indent
                result.append(prefix + ' '.join(ts))
                ts = []
                first_line = False
                current_length = indent;
                length = len(tk)

            ts.append(tk)
            current_length += length

        if ts:
            prefix = '' if first_line else ' ' * indent
            result.append(prefix + ' '.join(ts))

    tmp =  [_highlight(pattern, line) for line in result if line]
    if path_pattern:
        tmp = [_highlight(path_pattern, line, level=1) for line in tmp if line]
    return tmp

#-----------------------------------------------------------
# private
#-----------------------------------------------------------
def _mkid(lang_file):
    cmd = ['mkid', '-m', lang_file]
    process = subprocess.Popen(cmd,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    print(process.stdout.read())
    print(process.stderr.read())
    return True

def _is_cmd_exists(cmd):
    return 0 == subprocess.call(['which', cmd],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)

def _get_idutils_install_cmd():
    if platform.system() == 'Darwin':
        mgrs = {
               'port': "sudo port install idutils", # MacPorts
               'brew': "brew install idutils",      # Homebrew
            }
        for mgr, cmd in mgrs.items():
            if _is_cmd_exists(mgr):
                return cmd
        return ""
    else:
        return "sudo apt-get install id-utils"

def _get_gid_cmd():
    gid = 'gid'
    if platform.system() == 'Darwin':
        if not _is_cmd_exists(gid):
            gid = 'gid32'
    return gid

def _execute(args):
    # TODO(fcamel): add a global flag to turn on/off debug message.
    process = subprocess.Popen(args,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    text = process.stdout.read()
    try:
        text = text.decode('utf8')
    except Exception as e:
        print('-' * 80)
        print('\ntext: <%s>\nreturns non-utf8 result.' % text)
        print('-' * 80)
        result = []
        for line in text.split('\n'):
            try:
                line = line.decode('utf8')
                result.append(line)
            except Exception as e:
                print('-' * 80)
                print('%s: skip <%s>' % (e, line))
                print('-' * 80)
        return result
    return text.split('\n')

def _gid(pattern):
    cmd = [_get_gid_cmd(), pattern]
    return _execute(cmd)

def _lid(pattern, args):
    cmd = ['lid'] + args + [pattern]
    return _execute(cmd)

def _highlight(pattern, text, level=2):
    def red(text):
        return '\033[1;31m%s\033[0m' % text

    def green(text):
        return '\033[1;32m%s\033[0m' % text

    # Find all begin indexes of case-insensitive substring.
    begins = []
    base = 0
    pl = pattern.lower()
    tl = text.lower()
    while True:
        try:
            offset = tl.index(pl)
            begins.append(base + offset)
            tl = tl[offset + len(pl):]
            base += offset + len(pl)
        except Exception as e:
            break

    if not begins:
        return text

    # Highlight matched case-insensitive substrings.
    result = []
    last_end = 0
    for begin in begins:
        if begin > last_end:
            result.append(text[last_end:begin])
        end = begin + len(pattern)
        if level >= 2:
            result.append(red(text[begin:end]))
        else:
            result.append(green(text[begin:end]))
        last_end = end
    if last_end < len(text):
        result.append(text[last_end:])

    return ''.join(result)

def _show_list(matches, patterns, last_n, fold):
    def yellow(text):
        return '\033[1;33m%s\033[0m' % text

    def green(text):
        return '\033[1;32m%s\033[0m' % text

    def darkgreen(text):
        return '\033[0;32m%s\033[0m' % text

    def red(text):
        return '\033[1;31m%s\033[0m' % text

    def blue(text):
        return '\033[1;34m%s\033[0m' % text

    def gray(text):
        return '\033[1;30m%s\033[0m' % text

    def black(text):
        return '\033[0;30m%s\033[0m' % text

    os.system('clear')
    last_filename = ''
    filename_color = None
    need_print_filename = False
    for i, m in enumerate(matches):
        if m.filename != last_filename:
            need_print_filename = True
            if filename_color == green:
                filename_color = darkgreen
            else:
                filename_color = green
        else:
            need_print_filename = False
        if fold and m.filename == last_filename:
            continue

        #if not need_print_filename:
        #    filename_color = black

        last_filename = m.filename
        i += 1
        if i == last_n:
            print(blue('(%3d) %s:%d:%s' % (i, m.filename, m.line_num, m.text)))
        else:
            code = m.text
            for pattern in patterns:
                code = _highlight(pattern, code)
            print('(%s) %s:%s:%s' % (red('%3d' % i), filename_color(m.filename), yellow('%d' % m.line_num), code))

def _filter_statement(all_, exclude):
    matches = [m for m in all_ if re.search(';\s*$', m.text)]
    if not exclude:
        return matches
    return _subtract_list(all_, matches)

def _filter_pattern(matches, pattern):
    negative_symbol = '~'

    new_matches = []
    new_pattern = pattern[1:] if pattern.startswith(negative_symbol) else pattern
    for m in matches:
        if new_pattern == '=':  # special case:
            matched = not not re.search('[^=]=[^=]', m.text)
        else:
            matched = not not re.search('\\b%s\\b' % new_pattern, m.text)
        if pattern.startswith(negative_symbol):
            matched = not matched
        if matched:
            new_matches.append(m)

    return new_matches

def _filter_filename(all_, pattern, exclude):
    matched = [m for m in all_ if re.search(pattern, m.filename)]
    if not exclude:
        return matched
    return _subtract_list(all_, matched)

def _subtract_list(kept, removed):
    return [e for e in kept if e not in removed]

def _keep_possible_definition(all_, pattern):
    result = set()

    # C++: "::METHOD(...)"
    new_pattern = '::%s(' % pattern
    result.update(m for m in all_ if new_pattern in m.text)

    # C++: "METHOD() { ... }"
    new_pattern = pattern + ' *\(.*{.*}.*$'
    result.update(m for m in all_ if re.search(new_pattern, m.text))

    # Python: "def METHOD"
    new_pattern = 'def +' + pattern
    result.update(m for m in all_ if re.search(new_pattern, m.text))

    return result

def _find_possible_filename(pattern):
    def to_camelcase(word):
        '''
        Ref. http://stackoverflow.com/questions/4303492/how-can-i-simplify-this-conversion-from-underscore-to-camelcase-in-python
        '''
        return ''.join(x.capitalize() or '_' for x in word.split('_'))

    def to_underscore(name):
        '''
        Ref. http://stackoverflow.com/questions/1175208/elegant-python-function-to-convert-camelcase-to-camel-case/1176023#1176023
        '''
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    if re.search('[A-Z]', pattern):  # assume it's a camcelcase pattern
        return (to_underscore(pattern), pattern)
    else:  # assume it's an underscore pattern
        return (pattern, to_camelcase(pattern))

# TODO(fcamel): modulize filter actions and combine help message and filter actions together.
def _get_prompt_help():
    msg = (
        '\nSelect an action:'
        '\n* Input number to select a file. Multiple choices are allowed (e.g., type "1-3, 5")'
        '\n* Type "%s" / "%s" to keep / remove statements.'
        '\n* Type "%s" to switch between all matches and fold matches.'
        '\n* Type STRING (regex) to filter filename. !STRING means exclude '
        'the matched filename: '
        '\n* Type %s[PATTERN1 PATTERN2 ~PATTERN3 ...] to start over. '
        '\n  Type only "%s" to use the patterns from the command line.'
        '\n* Type ENTER to exit.'
        '\n'
        '\n>> ' % (A_KEEP_STATEMENT, A_CLEAN_STATEMENT,
                   A_FOLD, A_RESTART, A_RESTART)
    )
    return msg

def parse_number(line):
    '''
    Expected input:
        3
        3,5
        3, 5, 7-10
    '''
    ns = set()
    ts = line.split(',')
    for t in ts:
        try:
            ns.add(int(t))
            continue
        except Exception as e:
            pass

        m = re.match('(\d+)-(\d+)', t.strip())
        if m:
            from_, to = map(int, m.groups())
            ns.update(range(from_, to + 1))

    return sorted(ns)

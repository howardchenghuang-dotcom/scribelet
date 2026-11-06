"""iPhone Safari rules, found by testing in the iOS Simulator.

1. Safari capitalises the first letter of a text field. Logins are case-sensitive, so "demo" became
   "Demo" and the right password was rejected. Login fields must turn that off.
2. Safari zooms the whole page into any field whose text is under 16px, and does not zoom back.
   On phones every input, select and textarea must be at least 16px.
"""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

STATIC = Path(__file__).resolve().parents[1] / 'static'


class Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def handle_starttag(self, tag, attrs):
        if tag == 'input':
            self.inputs.append(dict(attrs))


class MobileSafariRules(unittest.TestCase):
    def test_typed_identifiers_are_not_auto_capitalised(self):
        page = Inputs()
        page.feed((STATIC / 'index.html').read_text(encoding='utf-8'))
        identifiers = [i for i in page.inputs
                       if i.get('autocomplete') in ('username', 'email') or i.get('type') == 'email']
        self.assertTrue(identifiers, 'the login field must be found')
        for field in identifiers:
            self.assertEqual(field.get('autocapitalize'), 'none', field)
            self.assertEqual(field.get('autocorrect'), 'off', field)

    def test_phone_fields_are_at_least_16px(self):
        css = (STATIC / 'style.css').read_text(encoding='utf-8')
        phone = re.search(r'@media\(max-width:650px\)\{(.*)\}\s*$', css, re.S)
        self.assertIsNotNone(phone, 'the phone layout block must exist')
        rule = re.search(r'input,select,textarea\{font-size:(\d+)px\}', phone.group(1))
        self.assertIsNotNone(rule, 'phones need an explicit font size for fields')
        self.assertGreaterEqual(int(rule.group(1)), 16)


if __name__ == '__main__':
    unittest.main()

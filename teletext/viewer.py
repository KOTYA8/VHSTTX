from dataclasses import dataclass


@dataclass(frozen=True)
class FastextLinkInfo:
    page_number: int | None
    subpage_number: int | None
    label: str
    enabled: bool


class DirectPageBuffer:
    valid_digits = '0123456789ABCDEF'
    valid_first_digits = '12345678'

    def __init__(self):
        self.clear()

    def clear(self):
        self._text = ''

    @property
    def text(self):
        return self._text

    def push(self, character):
        character = character.strip().upper()
        if len(character) != 1 or character not in self.valid_digits:
            return False
        if not self._text and character not in self.valid_first_digits:
            return False
        if len(self._text) >= 3:
            self._text = ''
        self._text += character
        return True

    def backspace(self):
        if not self._text:
            return False
        self._text = self._text[:-1]
        return True

    @property
    def complete(self):
        return len(self._text) == 3


class ServiceNavigator:
    def __init__(self, service):
        self._service = service
        self._pages = self._collect_pages(service)
        self._hex_pages_enabled = True
        if not self._pages:
            raise ValueError('Teletext service does not contain any pages.')
        self._current_page_number = self._pages[0]
        self._current_subpage_number = self._subpage_numbers(self._current_page_number)[0]

    @staticmethod
    def compose_page_number(magazine, page):
        return (int(magazine) << 8) | int(page)

    @staticmethod
    def split_page_number(page_number):
        page_number = int(page_number)
        return page_number >> 8, page_number & 0xff

    @staticmethod
    def parse_page_number(text):
        text = text.strip().upper()
        if text.startswith('P'):
            text = text[1:]
        if len(text) != 3:
            raise ValueError('Page number must be three hexadecimal digits, e.g. 100 or 1AF.')
        try:
            page_number = int(text, 16)
        except ValueError as exc:
            raise ValueError('Page number must contain hexadecimal digits only.') from exc
        magazine = page_number >> 8
        if magazine < 1 or magazine > 8:
            raise ValueError('Magazine number must be between 1 and 8.')
        return page_number

    @property
    def page_numbers(self):
        return tuple(self._navigable_pages())

    @property
    def current_page_number(self):
        return self._current_page_number

    @property
    def current_page_label(self):
        magazine, page = self.split_page_number(self._current_page_number)
        return f'P{magazine}{page:02X}'

    @property
    def current_subpage_number(self):
        return self._current_subpage_number

    @property
    def current_subpage(self):
        return self._page(self._current_page_number).subpages[self._current_subpage_number]

    @property
    def current_subpage_index(self):
        return self._subpage_numbers(self._current_page_number).index(self._current_subpage_number)

    @property
    def current_subpage_count(self):
        return len(self._subpage_numbers(self._current_page_number))

    @property
    def current_subpage_position(self):
        return self.current_subpage_index + 1, self.current_subpage_count

    def go_to_page_text(self, text):
        return self.go_to_page(self.parse_page_number(text))

    @staticmethod
    def is_decimal_page(page_number):
        _, page = ServiceNavigator.split_page_number(page_number)
        return all(character.isdigit() for character in f'{page:02X}')

    @property
    def hex_pages_enabled(self):
        return self._hex_pages_enabled

    def set_hex_pages_enabled(self, enabled):
        self._hex_pages_enabled = bool(enabled)
        navigable_pages = self._navigable_pages()
        if self._current_page_number not in navigable_pages:
            self._current_page_number = self._closest_navigable_page()
            self._current_subpage_number = self._subpage_numbers(self._current_page_number)[0]

    def go_to_page(self, page_number, subpage_number=None):
        if page_number not in self._navigable_pages():
            return False

        subpages = self._subpage_numbers(page_number)
        self._current_page_number = page_number
        if subpage_number in subpages:
            self._current_subpage_number = subpage_number
        else:
            self._current_subpage_number = subpages[0]
        return True

    def go_next_page(self):
        pages = self._navigable_pages()
        index = pages.index(self._current_page_number)
        return self.go_to_page(pages[(index + 1) % len(pages)])

    def go_prev_page(self):
        pages = self._navigable_pages()
        index = pages.index(self._current_page_number)
        return self.go_to_page(pages[(index - 1) % len(pages)])

    def go_next_subpage(self):
        subpages = self._subpage_numbers(self._current_page_number)
        index = subpages.index(self._current_subpage_number)
        self._current_subpage_number = subpages[(index + 1) % len(subpages)]
        return True

    def go_prev_subpage(self):
        subpages = self._subpage_numbers(self._current_page_number)
        index = subpages.index(self._current_subpage_number)
        self._current_subpage_number = subpages[(index - 1) % len(subpages)]
        return True

    def fastext_links(self):
        subpage = self.current_subpage
        if not subpage.has_packet(27, 0):
            return tuple(FastextLinkInfo(None, None, '---', False) for _ in range(4))

        links = []
        for link in subpage.fastext.links[:4]:
            page_number = self.compose_page_number(link.magazine, link.page)
            links.append(FastextLinkInfo(
            page_number=page_number,
            subpage_number=link.subpage,
            label=f'P{link.magazine}{link.page:02X}',
            enabled=page_number in self._navigable_pages(),
        ))
        return tuple(links)

    def go_to_fastext(self, index):
        link = self.fastext_links()[index]
        if not link.enabled or link.page_number is None:
            return False
        return self.go_to_page(link.page_number, link.subpage_number)

    def _page(self, page_number):
        magazine_number, page = self.split_page_number(page_number)
        magazine = self._service.magazines.get(magazine_number)
        if magazine is None:
            raise KeyError(page_number)
        result = magazine.pages.get(page)
        if result is None:
            raise KeyError(page_number)
        return result

    def _subpage_numbers(self, page_number):
        return tuple(sorted(self._page(page_number).subpages))

    def _navigable_pages(self):
        pages = [page_number for page_number in self._pages if self._page_allowed(page_number)]
        return pages if pages else list(self._pages)

    def _page_allowed(self, page_number):
        return self._hex_pages_enabled or self.is_decimal_page(page_number)

    def _closest_navigable_page(self):
        pages = self._navigable_pages()
        current_index = self._pages.index(self._current_page_number)
        for offset in range(len(self._pages)):
            candidate = self._pages[(current_index + offset) % len(self._pages)]
            if candidate in pages:
                return candidate
        return pages[0]

    @staticmethod
    def _collect_pages(service):
        pages = []
        for magazine_number, magazine in sorted(service.magazines.items()):
            for page_number, page in sorted(magazine.pages.items()):
                if page.subpages:
                    pages.append(ServiceNavigator.compose_page_number(magazine_number, page_number))
        return pages

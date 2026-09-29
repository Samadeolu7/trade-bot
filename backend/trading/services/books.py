"""Book accounting. An account holds several books: the "manual" book
(the people trading it) and one per bot. Each book's balances are the
sum of its ledger entries, so a bot can only spend its own allocation
and only sell what it bought."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from bot.broker.base import BrokerError
from trading.models import MANUAL_BOOK, LedgerEntry, TradingAccount
from trading.services.brokers import quote_source

ZERO = Decimal(0)


def book_balances(account: TradingAccount, book: str) -> dict[str, Decimal]:
    rows = (
        LedgerEntry.objects.filter(account=account, book=book)
        .values("asset").annotate(total=Sum("amount"))
    )
    return {r["asset"]: r["total"] for r in rows if r["total"]}


def all_book_balances(account: TradingAccount) -> dict[str, dict[str, Decimal]]:
    rows = LedgerEntry.objects.filter(account=account).values("book", "asset").annotate(total=Sum("amount"))
    books: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for r in rows:
        books[r["book"]][r["asset"]] = r["total"]
    books.setdefault(MANUAL_BOOK, {})
    return dict(books)


def marks(account: TradingAccount, assets) -> dict[str, Decimal]:
    """Mid price, in the quote asset, for each non-quote asset held. An
    asset whose price can't be fetched is left out, and callers treat it
    as unpriced rather than worth zero."""
    result = {}
    for asset in assets:
        if asset == account.quote_asset:
            continue
        try:
            result[asset] = quote_source(account.venue).quote(f"{asset}/{account.quote_asset}").mid
        except BrokerError:
            continue
    return result


def equity(balances: dict[str, Decimal], quote_asset: str, prices: dict[str, Decimal]) -> Decimal:
    total = balances.get(quote_asset, ZERO)
    for asset, amount in balances.items():
        if asset != quote_asset and asset in prices:
            total += amount * prices[asset]
    return total


def book_equity(account: TradingAccount, book: str, prices: dict[str, Decimal] | None = None) -> Decimal:
    balances = book_balances(account, book)
    if prices is None:
        prices = marks(account, balances)
    return equity(balances, account.quote_asset, prices)


def post(account: TradingAccount, book: str, kind: str, asset: str, amount: Decimal, order=None, note: str = ""):
    return LedgerEntry.objects.create(
        account=account, book=book, kind=kind, asset=asset, amount=amount, order=order, note=note
    )

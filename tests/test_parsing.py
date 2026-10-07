from datetime import date

from radar.parsing.classifier import (
    classify_form,
    classify_items,
    classify_text,
    is_press_wire,
    is_split_only,
)
from radar.parsing.split_text import parse_split_text
from radar.providers.nasdaq import parse_rows
from radar.providers.sec import html_to_text, tickers_from_display


class TestSplitText:
    def test_digit_ratio_and_effective_date(self):
        text = ("The Company announced a 1-for-20 reverse stock split. The common stock will begin "
                "trading on a split-adjusted basis on September 22, 2026 at market open.")
        p = parse_split_text(text)
        assert p.ratio == 20 and p.effective_date == date(2026, 9, 22)

    def test_word_ratio(self):
        p = parse_split_text("approved a one-for-fifteen reverse split of the common stock")
        assert p.ratio == 15

    def test_colon_ratio_ignores_times(self):
        p = parse_split_text("reverse stock split at a ratio of 1:25, effective at 12:01 a.m. on "
                             "October 1, 2026, market opens 9:30 a.m.")
        assert p.ratio == 25 and p.effective_date == date(2026, 10, 1)

    def test_range_in_proxy(self):
        p = parse_split_text("to effect a reverse stock split at a ratio ranging from 1-for-5 to 1-for-50")
        assert p.ratio == 50 and "rango" in p.ratio_text

    def test_forward_split_ignored(self):
        assert parse_split_text("a 2-for-1 forward split, not a reverse split").ratio is None

    def test_no_mention(self):
        assert parse_split_text("quarterly results 1-for-10").ratio is None


class TestClassifier:
    def test_items(self):
        cats = classify_items("1.01,9.01")
        assert cats == [("Contrato/acuerdo material", "alto")]

    def test_split_only(self):
        assert is_split_only("3.03,5.03,9.01")
        assert not is_split_only("3.03,5.03,7.01,9.01")

    def test_forms(self):
        assert classify_form("424B5")[0].startswith("Financiacion")
        assert classify_form("6-K")[0].startswith("Comunicado")
        assert classify_form("4") is None

    def test_text_fda(self):
        cats = classify_text("XYZ Receives FDA Approval for lead candidate")
        assert cats[0] == ("FDA: aprobacion/autorizacion", "alto")

    def test_text_crl(self):
        assert classify_text("Company receives Complete Response Letter")[0][0].startswith("FDA: rechazo")

    def test_text_nasdaq(self):
        cats = classify_text("regained compliance with Nasdaq minimum bid price requirement")
        assert ("Cumplimiento/incumplimiento de listado (Nasdaq/NYSE)", "medio") in cats

    def test_press_wire(self):
        assert is_press_wire("GlobeNewswire")
        assert not is_press_wire("Simply Wall St.")


class TestProviders:
    def test_tickers_from_display(self):
        assert tickers_from_display("VisionWave Holdings, Inc.  (VWAV, VWAVW)  (CIK 0002038439)") == ("VWAV", "VWAVW")
        assert tickers_from_display("Private Co (CIK 0000001)") == ()

    def test_html_to_text(self):
        assert html_to_text("<p>Hello&nbsp;<b>world</b></p><script>x</script>") == "Hello world"

    def test_nasdaq_rows(self):
        rows = parse_rows([
            {"symbol": "ABC", "name": "Abc", "lastsale": "$1.20", "netchange": "0.20",
             "pctchange": "20.00%", "volume": "1,000", "marketCap": "5000000.00"},
            {"symbol": "BRK/B", "name": "B", "lastsale": "NA", "netchange": "", "pctchange": "",
             "volume": "", "marketCap": ""},
        ])
        assert rows[0].price == 1.2 and rows[0].prev_close == 1.0 and rows[0].change_pct == 20
        assert rows[1].ticker == "BRK-B" and rows[1].price is None

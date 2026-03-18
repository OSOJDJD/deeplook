"""
SEC EDGAR fetcher — financials, 8-K signals, 13F holdings, insider transactions.

Requires: edgartools>=5.23.0
Set EDGAR_IDENTITY env var (e.g. "Your Name your@email.com") per SEC fair-access policy.
"""

import asyncio
import os
from datetime import date, timedelta


def _setup_edgar():
    from edgar import Company, set_identity
    identity = os.environ.get("EDGAR_IDENTITY", "DeepLook Research deeplook@example.com")
    set_identity(identity)
    return Company


async def fetch_sec_edgar(ticker: str) -> dict:
    result: dict = {"source": "sec_edgar", "success": True, "ticker": ticker}

    try:
        Company = await asyncio.wait_for(asyncio.to_thread(_setup_edgar), timeout=10)
        company = await asyncio.wait_for(asyncio.to_thread(Company, ticker), timeout=10)
    except Exception as e:
        return {"source": "sec_edgar", "success": False, "error": str(e)}

    # ── 1. Financials (latest 10-K or 10-Q) ─────────────────────────────────
    def _get_financials():
        fin = company.get_financials()
        inc_df = fin.income_statement().to_dataframe()
        cf_df = fin.cash_flow_statement().to_dataframe()

        date_cols = [c for c in inc_df.columns if c not in ("concept", "label", "standard_concept")]
        if not date_cols:
            return {}
        latest = date_cols[0]

        def _first_val(df, concept):
            rows = df[df["concept"] == concept]
            if rows.empty:
                return None
            v = rows.iloc[0][latest]
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        revenue = _first_val(inc_df, "us-gaap_Revenues")
        op_income = _first_val(inc_df, "us-gaap_OperatingIncomeLoss")
        net_income = _first_val(inc_df, "us-gaap_NetIncomeLoss")
        eps = _first_val(inc_df, "us-gaap_EarningsPerShareBasic")

        cf_date_cols = [c for c in cf_df.columns if c not in ("concept", "label", "standard_concept")]
        op_cf, capex = None, None
        if cf_date_cols:
            cf_latest = cf_date_cols[0]

            def _cf_val(concept):
                rows = cf_df[cf_df["concept"] == concept]
                if rows.empty:
                    return None
                v = rows.iloc[0][cf_latest]
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            op_cf = _cf_val("us-gaap_NetCashProvidedByUsedInOperatingActivities")
            capex = _cf_val("us-gaap_PaymentsToAcquireProductiveAssets")

        return {
            "period": latest,
            "revenue": revenue,
            "net_income": net_income,
            "operating_margin_pct": round(op_income / revenue * 100, 1) if revenue and op_income else None,
            "eps_basic": eps,
            "free_cash_flow": (op_cf - abs(capex)) if op_cf is not None and capex is not None else None,
        }

    try:
        result["financials"] = await asyncio.wait_for(asyncio.to_thread(_get_financials), timeout=10)
    except Exception as e:
        result["financials"] = {"error": str(e)}

    # ── 2. Recent 8-K filings (latest 3) ────────────────────────────────────
    def _get_8k():
        filings = company.get_filings(form="8-K").head(3)
        out = []
        for f in filings:
            items_str = f.items or ""
            out.append({
                "filing_date": str(f.filing_date),
                "items": [i.strip() for i in items_str.split(",") if i.strip()],
            })
        return out

    try:
        result["recent_filings"] = await asyncio.wait_for(asyncio.to_thread(_get_8k), timeout=10)
    except Exception:
        result["recent_filings"] = []

    # ── 3. Institutional holders (yfinance, top 10) ─────────────────────────
    def _get_institutional_holders():
        import yfinance as yf
        tk = yf.Ticker(ticker)
        holders = tk.institutional_holders
        if holders is None or holders.empty:
            return []
        top = holders.head(10)
        out = []
        for _, row in top.iterrows():
            out.append({
                "holder_name": row.get("Holder"),
                "shares": row.get("Shares"),
                "value": row.get("Value"),
                "pct_held": row.get("pctHeld"),
            })
        return out

    try:
        result["institutional_holders"] = await asyncio.wait_for(
            asyncio.to_thread(_get_institutional_holders), timeout=10
        )
    except Exception:
        result["institutional_holders"] = []

    # ── 4. Major shareholders (yfinance, top 10) ────────────────────────────
    def _get_major_shareholders():
        import yfinance as yf
        tk = yf.Ticker(ticker)
        holders = tk.institutional_holders
        if holders is None or holders.empty:
            return []
        top = holders.head(10)
        out = []
        for _, row in top.iterrows():
            out.append({
                "name": row.get("Holder"),
                "shares": row.get("Shares"),
                "value": row.get("Value"),
                "pct_held": row.get("pctHeld"),
            })
        return out

    try:
        result["major_shareholders"] = await asyncio.wait_for(
            asyncio.to_thread(_get_major_shareholders), timeout=10
        )
    except Exception:
        result["major_shareholders"] = []

    # ── 5. Insider transactions (Form 4, last 90 days) ───────────────────────
    def _get_form4():
        cutoff = date.today() - timedelta(days=90)
        filings = company.get_filings(form="4").head(10)
        out = []
        for f in filings:
            try:
                if str(f.filing_date) < str(cutoff):
                    continue
                obj = f.obj()
                for act in obj.get_transaction_activities():
                    out.append({
                        "name": obj.insider_name,
                        "transaction_type": act.transaction_type,
                        "shares": act.shares,
                        "date": str(f.filing_date),
                    })
            except Exception:
                continue
        return out

    try:
        result["insider_transactions"] = await asyncio.wait_for(asyncio.to_thread(_get_form4), timeout=10)
    except Exception:
        result["insider_transactions"] = []

    return result

"""
Canonical indicator registry — seeded into the `indicators` table on first run.

Contains all 30 scored indicators (v1) plus ~15 supporting series that appear
on indicator library pages but don't enter the headline score.

Call seed(client) once; subsequent calls are idempotent (INSERT OR IGNORE on slug).
"""

from __future__ import annotations

import libsql_client
import structlog

from ingestion.db import upsert_indicator

log = structlog.get_logger(__name__)

# ── Sub-score slugs (canonical names used in scoring.py) ─────────────────────
LABOR = "labor_income"
BALANCE = "household_balance_sheet"
CREDIT = "credit_stress"
SPENDING = "spending_demand"
SENTIMENT = "sentiment_expectations"
INFLATION = "inflation_affordability"
BIGTICKET = "big_ticket_affordability"

# fmt: off
INDICATORS: list[dict] = [

    # ── Sub-score 1: Labor & Income (headline weight 20%) ─────────────────────
    # 5 indicators, each weight 0.20 within sub-score
    {
        "slug": "unrate",
        "series_id": "UNRATE",
        "source": "fred",
        "name": "Unemployment Rate (U-3)",
        "subscore": LABOR,
        "frequency": "monthly",
        "units": "percent",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.20,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "why_it_matters_md": (
            "The unemployment rate is the single most-watched labor market indicator. "
            "Rising unemployment erodes household income and spending power, increases "
            "credit stress, and typically precedes consumer demand contraction. "
            "A low rate signals that most households have stable income flows."
        ),
        "limitations_md": (
            "U-3 undercounts labor underutilization — it excludes discouraged workers "
            "and those working part-time involuntarily (captured in U-6). "
            "Revisions are modest but the series is subject to benchmark revisions annually."
        ),
    },
    {
        "slug": "payems_3mo_avg",
        "series_id": "PAYEMS",
        "source": "fred",
        "name": "Nonfarm Payrolls — 3-Month Avg Change",
        "subscore": LABOR,
        "frequency": "monthly",
        "units": "thousands of jobs",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.20,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": "Transform: MoM change of raw level, then 3-month rolling mean.",
        "why_it_matters_md": (
            "Monthly payroll gains are the most-cited measure of job creation. "
            "The 3-month average smooths volatile single-month readings. "
            "Sustained job growth above ~150k/month typically supports consumer spending; "
            "below zero signals contraction."
        ),
        "limitations_md": (
            "Subject to substantial revisions — the initial estimate often differs "
            "from the final benchmark by 100k+ jobs. The 3-month smoothing "
            "helps but doesn't eliminate revision risk."
        ),
    },
    {
        "slug": "ic4wsa",
        "series_id": "IC4WSA",
        "source": "fred",
        "name": "Initial Jobless Claims — 4-Week Average",
        "subscore": LABOR,
        "frequency": "weekly",
        "units": "thousands",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.20,
        "lcl_class": "leading",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "why_it_matters_md": (
            "Weekly initial claims are the most timely labor market indicator, "
            "released every Thursday for the prior week. The 4-week moving average "
            "smooths holiday distortions. Rising claims reliably precede payroll "
            "weakness by 4–8 weeks."
        ),
    },
    {
        "slug": "ccsa",
        "series_id": "CCSA",
        "source": "fred",
        "name": "Continued Claims",
        "subscore": LABOR,
        "frequency": "weekly",
        "units": "thousands",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.20,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "why_it_matters_md": (
            "Continued claims measure the stock of workers actively receiving "
            "unemployment benefits — a proxy for how hard it is to find a new job. "
            "Rising continued claims signal that layoffs are not being absorbed "
            "quickly by rehiring."
        ),
    },
    {
        "slug": "real_ahe_yoy",
        "series_id": "CES0500000013",
        "source": "fred",
        "name": "Real Average Hourly Earnings (YoY)",
        "subscore": LABOR,
        "frequency": "monthly",
        "units": "percent change",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.20,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "2007-01-01",
        "notes": "Transform: YoY % change. Available from 2007.",
        "why_it_matters_md": (
            "Real (inflation-adjusted) wage growth is the key signal of whether "
            "workers' purchasing power is rising or falling. Negative real wage "
            "growth — as seen in 2021–22 — directly squeezes consumer budgets "
            "even when nominal pay is rising."
        ),
    },

    # ── Sub-score 2: Household Balance Sheet (headline weight 15%) ────────────
    # 4 indicators, each weight 0.25
    {
        "slug": "psavert",
        "series_id": "PSAVERT",
        "source": "fred",
        "name": "Personal Saving Rate",
        "subscore": BALANCE,
        "frequency": "monthly",
        "units": "percent of DPI",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "why_it_matters_md": (
            "The saving rate measures the share of disposable income households "
            "are not spending. A higher rate signals financial resilience and "
            "future spending capacity; very low rates (below 3%) historically "
            "precede financial stress as there is no buffer for income shocks."
        ),
        "limitations_md": (
            "BEA revises this series substantially with each NIPA benchmark "
            "revision. The 2022 revision raised the 2021 saving rate significantly. "
            "Also, aggregate rates mask wide dispersion across income quintiles."
        ),
    },
    {
        "slug": "real_dpi_yoy",
        "series_id": "DSPIC96",
        "source": "fred",
        "name": "Real Disposable Personal Income (YoY)",
        "subscore": BALANCE,
        "frequency": "monthly",
        "units": "percent change",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": "Transform: YoY % change of DSPIC96 (chained 2017 dollars).",
        "why_it_matters_md": (
            "Real DPI growth captures the combined effect of wages, transfers, "
            "taxes, and inflation on household purchasing power. Sustained "
            "positive real DPI growth is a necessary condition for healthy "
            "consumer spending without balance-sheet deterioration."
        ),
    },
    {
        "slug": "tdsp",
        "series_id": "TDSP",
        "source": "fred",
        "name": "Household Debt Service Ratio",
        "subscore": BALANCE,
        "frequency": "quarterly",
        "units": "percent of DPI",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "lagging",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "why_it_matters_md": (
            "The DSR measures the share of after-tax income households use to "
            "service debt (mortgage + consumer). A rising DSR compresses "
            "discretionary spending and is one of the most reliable early signals "
            "of consumer financial stress. Pre-GFC the DSR rose to 13%+; "
            "post-COVID deleveraging pushed it below 9%."
        ),
    },
    {
        "slug": "networth_dpi_ratio",
        "series_id": "BOGZ1FL192090005Q",
        "source": "fred",
        "name": "Household Net Worth / Disposable Income Ratio",
        "subscore": BALANCE,
        "frequency": "quarterly",
        "units": "ratio",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "lagging",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": (
            "Derived: BOGZ1FL192090005Q (household net worth, $B nominal) "
            "divided by DSPI×4 (annualized nominal DPI). "
            "Requires DSPI (supporting series) to be ingested."
        ),
        "why_it_matters_md": (
            "The net worth / income ratio captures household wealth relative "
            "to their income base — a measure of financial cushion. "
            "Equity and home price appreciation drive it higher; market crashes "
            "and debt build-up pull it down. High levels support spending even "
            "when income growth slows (wealth effect)."
        ),
    },

    # ── Sub-score 3: Credit Stress (headline weight 20%) ──────────────────────
    # 5 indicators, each weight 0.20
    {
        "slug": "drcclacbs",
        "series_id": "DRCCLACBS",
        "source": "fred",
        "name": "Credit Card Delinquency Rate — All Commercial Banks",
        "subscore": CREDIT,
        "frequency": "quarterly",
        "units": "percent",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.20,
        "lcl_class": "lagging",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "why_it_matters_md": (
            "Credit card delinquency rates (90+ days past due) at all commercial "
            "banks are the most comprehensive public measure of consumer credit "
            "distress. Rising delinquencies signal that households are struggling "
            "to service revolving debt — often a precursor to charge-offs and "
            "lender tightening."
        ),
    },
    {
        "slug": "drclacbs",
        "series_id": "DRCLACBS",
        "source": "fred",
        "name": "Consumer Loan Delinquency Rate — All Commercial Banks",
        "subscore": CREDIT,
        "frequency": "quarterly",
        "units": "percent",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.20,
        "lcl_class": "lagging",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "why_it_matters_md": (
            "Broader than credit card delinquency, this captures auto loans, "
            "personal loans, student loans, and other consumer credit. "
            "Rising delinquencies across multiple loan types signal systemic "
            "consumer financial stress rather than product-specific normalization."
        ),
    },
    {
        "slug": "corccacbs",
        "series_id": "CORCCACBS",
        "source": "fred",
        "name": "Credit Card Charge-Off Rate — All Commercial Banks",
        "subscore": CREDIT,
        "frequency": "quarterly",
        "units": "percent",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.20,
        "lcl_class": "lagging",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "why_it_matters_md": (
            "Charge-offs represent losses banks recognize on uncollectable debt. "
            "They lag delinquencies by 1–3 quarters and are the 'final verdict' "
            "on consumer credit stress. Elevated charge-offs typically trigger "
            "tighter lending standards, creating a credit-contraction feedback loop."
        ),
    },
    {
        "slug": "nyfed_serious_delinq",
        "series_id": "NYFED_HHDC_CC_SERIOUS_DELINQ",
        "source": "nyfed",
        "name": "NY Fed HHDC — Credit Card Transition to Serious Delinquency (90+)",
        "subscore": CREDIT,
        "frequency": "quarterly",
        "units": "percent",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.20,
        "lcl_class": "lagging",
        "is_scored": True,
        "backfill_start": "2003-01-01",
        "notes": (
            "Source: NY Fed Household Debt and Credit (HHDC) XLSX. "
            "Published quarterly ~3 months after quarter end. "
            "Scraped from newyorkfed.org/microeconomics/hhdc."
        ),
        "why_it_matters_md": (
            "The NY Fed's HHDC transition rates are the single best leading indicator "
            "of consumer credit stress — they show what share of balances are "
            "newly entering serious delinquency each quarter. This is more sensitive "
            "than the stock delinquency rate and captures deterioration earlier."
        ),
    },
    {
        "slug": "drtsclcc",
        "series_id": "DRTSCLCC",
        "source": "fred",
        "name": "SLOOS — Net % Banks Tightening Consumer Credit Card Standards",
        "subscore": CREDIT,
        "frequency": "quarterly",
        "units": "net percent",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.20,
        "lcl_class": "leading",
        "is_scored": True,
        "backfill_start": "1996-01-01",
        "why_it_matters_md": (
            "The Senior Loan Officer Opinion Survey (SLOOS) measures whether banks "
            "are tightening or loosening consumer lending standards. Net tightening "
            "is a leading indicator of credit availability contraction — which "
            "precedes consumer spending weakness by 2–4 quarters."
        ),
    },

    # ── Sub-score 4: Spending & Demand (headline weight 15%) ──────────────────
    # 4 indicators, each weight 0.25
    {
        "slug": "real_pce_mom_ann",
        "series_id": "PCEC96",
        "source": "fred",
        "name": "Real PCE — 3-Month Annualized Growth Rate",
        "subscore": SPENDING,
        "frequency": "monthly",
        "units": "percent annualized",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": (
            "Transform: MoM % change, then 3-month rolling mean, then annualize (×12). "
            "Scores the momentum of consumer spending, not the level."
        ),
        "why_it_matters_md": (
            "Real Personal Consumption Expenditures (PCE) is the broadest and "
            "most comprehensive measure of consumer spending — goods and services, "
            "covering ~70% of GDP. The 3-month annualized rate captures spending "
            "momentum better than a single month or YoY comparison."
        ),
    },
    {
        "slug": "rrsfs_yoy",
        "series_id": "RRSFS",
        "source": "fred",
        "name": "Advance Real Retail & Food Services Sales (YoY)",
        "subscore": SPENDING,
        "frequency": "monthly",
        "units": "percent change",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "1993-01-01",
        "notes": "Transform: YoY % change.",
        "why_it_matters_md": (
            "Advance retail sales (the 'control group' excluding autos, gas, "
            "building materials, food services) is the most widely cited "
            "monthly spending indicator. YoY comparison removes seasonality "
            "and is a direct input to GDP spending estimates."
        ),
    },
    {
        "slug": "tsa_throughput_vs2019",
        "series_id": "TSA_THROUGHPUT_VS2019",
        "source": "tsa",
        "name": "TSA Daily Checkpoint Throughput vs. 2019 (7-Day Avg)",
        "subscore": SPENDING,
        "frequency": "daily",
        "units": "percent of 2019 baseline",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "2020-01-01",
        "notes": (
            "Scraped from tsa.gov/travel/passenger-volumes. "
            "Transform: (current_day_travelers / same_day_2019_travelers) × 100."
        ),
        "why_it_matters_md": (
            "TSA throughput is the most real-time measure of consumer travel demand. "
            "Travel spending (airfare, hotels, car rentals) is highly discretionary — "
            "pullbacks signal weakening consumer confidence before it appears in "
            "monthly PCE data. The 2019 comparison removes COVID-era distortions."
        ),
    },
    {
        "slug": "real_pce_food_svcs_yoy",
        "series_id": "DFXARC1Q027SBEA",
        "source": "bea",
        "name": "Real PCE — Food Services & Accommodations (YoY)",
        "subscore": SPENDING,
        "frequency": "monthly",
        "units": "percent change",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "leading",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": (
            "From BEA NIPA Table 2.4.6U. "
            "Transform: YoY % change of the food services & accommodations sub-component."
        ),
        "why_it_matters_md": (
            "Restaurant and hotel spending is among the most income-elastic "
            "consumer categories — one of the first to be cut when household "
            "budgets tighten. It also leads PCE by capturing changes in "
            "discretionary behavior before they show up in broader measures."
        ),
    },

    # ── Sub-score 5: Sentiment & Expectations (headline weight 10%) ───────────
    # 3 indicators, each weight 0.333
    {
        "slug": "umcsent",
        "series_id": "UMCSENT",
        "source": "fred",
        "name": "University of Michigan Consumer Sentiment Index",
        "subscore": SENTIMENT,
        "frequency": "monthly",
        "units": "index (1966Q1=100)",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.333,
        "lcl_class": "leading",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": (
            "Copyright: University of Michigan. "
            "FRED republishes with 1-month delay per UMich request. "
            "Required citation: 'Surveys of Consumers, University of Michigan "
            "© [UMCSENT], retrieved from FRED.'"
        ),
        "why_it_matters_md": (
            "UMich sentiment is a 50-year monthly survey of household views on "
            "current and expected financial conditions. It leads consumer spending "
            "by 1–3 months and is particularly predictive for big-ticket purchases. "
            "Drops to 50s and below have historically coincided with recessions."
        ),
    },
    {
        "slug": "cscicp03",
        "series_id": "CSCICP03USM665S",
        "source": "fred",
        "name": "OECD Consumer Confidence Indicator (Conference Board Proxy)",
        "subscore": SENTIMENT,
        "frequency": "monthly",
        "units": "index",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.333,
        "lcl_class": "leading",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": (
            "Conference Board CCI redistribution is prohibited per their Terms of Use. "
            "We use CSCICP03USM665S (OECD US Consumer Confidence) as the charted proxy; "
            "headline Conference Board number is cited with a link-out to their press release."
        ),
        "why_it_matters_md": (
            "The OECD Consumer Confidence Indicator for the U.S. tracks similar "
            "dimensions to the Conference Board CCI — present situation and "
            "expectations. While not identical, it correlates closely and is "
            "freely distributable from FRED."
        ),
    },
    {
        "slug": "nyfed_sce_miss_payment",
        "series_id": "NYFED_SCE_MISS_PAYMENT",
        "source": "nyfed",
        "name": "NY Fed SCE — Probability of Missing a Minimum Debt Payment",
        "subscore": SENTIMENT,
        "frequency": "monthly",
        "units": "percent mean probability",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.333,
        "lcl_class": "leading",
        "is_scored": True,
        "backfill_start": "2013-01-01",
        "notes": (
            "Source: NY Fed Survey of Consumer Expectations (SCE), monthly. "
            "Released on second Monday of each month. Scraped from newyorkfed.org/microeconomics/sce."
        ),
        "why_it_matters_md": (
            "The NY Fed SCE directly asks households the probability they will miss "
            "a minimum debt payment in the next 3 months. This forward-looking "
            "self-assessment is among the best leading indicators of actual delinquency "
            "increases — it rises 2–4 quarters before reported delinquency rates spike."
        ),
    },

    # ── Sub-score 6: Inflation & Affordability (headline weight 10%) ──────────
    # 4 indicators, each weight 0.25
    {
        "slug": "cpi_yoy",
        "series_id": "CPIAUCSL",
        "source": "fred",
        "name": "Headline CPI (YoY)",
        "subscore": INFLATION,
        "frequency": "monthly",
        "units": "percent change",
        "higher_is_better": None,  # special: proximity to 2%
        "scoring_type": "proximity_2pct",
        "weight_in_subscore": 0.25,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": "Transform: YoY % change. Scored by proximity to 2% target.",
        "why_it_matters_md": (
            "Headline CPI captures the full cost-of-living change for U.S. urban "
            "consumers. Both high inflation (>4%) and deflation (<0%) are negative "
            "for consumer health — one erodes purchasing power, the other signals "
            "demand collapse. The scoring targets 2% as the Fed's optimal level."
        ),
    },
    {
        "slug": "core_cpi_yoy",
        "series_id": "CPILFESL",
        "source": "fred",
        "name": "Core CPI — ex Food and Energy (YoY)",
        "subscore": INFLATION,
        "frequency": "monthly",
        "units": "percent change",
        "higher_is_better": None,
        "scoring_type": "proximity_2pct",
        "weight_in_subscore": 0.25,
        "lcl_class": "lagging",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": "Transform: YoY % change. Scored by proximity to 2% target.",
        "why_it_matters_md": (
            "Core CPI strips out volatile food and energy prices to reveal the "
            "underlying inflation trend. It is the Fed's preferred near-term "
            "inflation gauge for monetary policy decisions, and its persistence "
            "above 2% drove the 2022–24 rate hiking cycle that compressed "
            "consumer affordability."
        ),
    },
    {
        "slug": "shelter_cpi_yoy",
        "series_id": "CUSR0000SAH1",
        "source": "fred",
        "name": "CPI Shelter (YoY)",
        "subscore": INFLATION,
        "frequency": "monthly",
        "units": "percent change",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "lagging",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": "Transform: YoY % change. Lower is better (less housing cost pressure).",
        "why_it_matters_md": (
            "Shelter is the largest CPI component (~36% of the total basket). "
            "Because it lags actual rental market changes by 12–18 months, "
            "it is a particularly sticky source of cost-of-living pressure. "
            "Shelter CPI stayed above 5% through 2024 even as market rents cooled."
        ),
    },
    {
        "slug": "eia_gas_price",
        "series_id": "EIA_GAS_US_REGULAR",
        "source": "eia",
        "name": "EIA U.S. Regular Retail Gasoline Price (Weekly Avg)",
        "subscore": INFLATION,
        "frequency": "weekly",
        "units": "dollars per gallon",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": (
            "EIA API v2 series: PET.EMM_EPMR_PTE_NUS_DPG.W. "
            "Updated Mondays ~5pm ET."
        ),
        "why_it_matters_md": (
            "Gas prices are the most visible daily price signal for consumers. "
            "Each $0.10/gallon increase costs the average U.S. household ~$100/year. "
            "High gas prices are regressive (hit lower-income households harder) "
            "and crowd out discretionary spending, especially in car-dependent markets."
        ),
    },

    # ── Sub-score 7: Big-Ticket Affordability (headline weight 10%) ───────────
    # 4 scored indicators at weight 0.25 each; Manheim MUVVI is context_only
    {
        "slug": "mortgage30us",
        "series_id": "MORTGAGE30US",
        "source": "fred",
        "name": "30-Year Fixed Mortgage Rate (PMMS)",
        "subscore": BIGTICKET,
        "frequency": "weekly",
        "units": "percent",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "leading",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": (
            "Freddie Mac Primary Mortgage Market Survey, published Thursdays. "
            "Available via FRED MORTGAGE30US."
        ),
        "why_it_matters_md": (
            "The 30-year fixed mortgage rate directly determines monthly payment "
            "affordability for new home purchases. The 2022–24 rate spike from 3% "
            "to 7%+ locked out millions of buyers and froze existing homeowners "
            "with sub-4% mortgages, creating an unprecedented housing market lock-in."
        ),
    },
    {
        "slug": "manheim_muvvi",
        "series_id": "MANHEIM_MUVVI",
        "source": "manheim",
        "name": "Manheim Used Vehicle Value Index",
        "subscore": BIGTICKET,
        "frequency": "monthly",
        "units": "index (Jan 1995=100)",
        "higher_is_better": None,  # context only — no directional scoring
        "scoring_type": "context_only",
        "weight_in_subscore": 0.0,  # excluded from scoring; weight redistributed
        "lcl_class": "coincident",
        "is_scored": False,
        "backfill_start": "1995-01-01",
        "notes": (
            "Scraped from publish.manheim.com on the 5th business day of each month. "
            "Context-only indicator: directional scoring is ambiguous (high used car "
            "prices = affordability stress, but can also signal demand strength). "
            "Displayed on indicator page only."
        ),
        "why_it_matters_md": (
            "The Manheim UVVI is the gold standard for used vehicle wholesale prices. "
            "Used car prices spiked 30%+ in 2021–22 due to new car shortages, "
            "dramatically increasing auto payments and insurance for buyers. "
            "It provides context for the auto affordability picture."
        ),
    },
    {
        "slug": "new_auto_loan_rate",
        "series_id": "RIFLPBCIANM72NM",
        "source": "fred",
        "name": "Commercial Bank New Auto Loan Rate — 72-Month",
        "subscore": BIGTICKET,
        "frequency": "monthly",
        "units": "percent",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "2011-01-01",
        "notes": "From Fed G.19 Consumer Credit release.",
        "why_it_matters_md": (
            "The 72-month auto loan rate (the most common term for new vehicle "
            "financing) directly determines monthly car payment affordability. "
            "Combined with elevated new and used car prices, auto loan rates "
            "above 7% pushed median monthly payments above $700, straining "
            "lower- and middle-income household budgets."
        ),
    },
    {
        "slug": "cc_interest_rate",
        "series_id": "TERMCBCCALLNS",
        "source": "fred",
        "name": "Credit Card Interest Rate — All Accounts Assessed Interest",
        "subscore": BIGTICKET,
        "frequency": "quarterly",
        "units": "percent",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": "From Fed G.19. Quarterly. Hit record highs above 22% in 2023–24.",
        "why_it_matters_md": (
            "Credit card APRs reached record levels above 22% in 2023–24 as the "
            "Fed's rate hike cycle fed directly into variable-rate card pricing. "
            "For the ~40% of cardholders who carry a balance, this represents a "
            "direct tax on spending capacity and accelerates debt accumulation."
        ),
    },
    {
        "slug": "housing_affordability",
        "series_id": "HOUSING_AFFORD_COMPOSITE",
        "source": "derived",
        "name": "Housing Affordability Composite",
        "subscore": BIGTICKET,
        "frequency": "monthly",
        "units": "index (lower = less affordable)",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.25,
        "lcl_class": "coincident",
        "is_scored": True,
        "backfill_start": "1990-01-01",
        "notes": (
            "Derived: MORTGAGE30US monthly avg × proxy_median_home_price / "
            "proxy_median_household_income. "
            "Uses MSPUS (median sales price of houses sold, FRED) and "
            "MEHOINUSA672N (median household income, Census via FRED, annual, interpolated). "
            "Higher index = less affordable = lower score."
        ),
        "why_it_matters_md": (
            "The housing affordability composite captures the monthly payment burden "
            "of purchasing a median-priced home relative to median household income. "
            "In 2023–24 it reached its worst level since the early 1980s, effectively "
            "excluding first-time buyers and suppressing household formation."
        ),
    },

    # ── Supporting series (is_scored=False, ingested for transforms/library) ───
    {
        "slug": "jtsjol",
        "series_id": "JTSJOL",
        "source": "fred",
        "name": "JOLTS Job Openings (Total Nonfarm)",
        "subscore": LABOR,
        "frequency": "monthly",
        "units": "thousands",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "leading",
        "is_scored": False,
        "backfill_start": "2001-01-01",
    },
    {
        "slug": "jts1000qur",
        "series_id": "JTS1000QUR",
        "source": "fred",
        "name": "JOLTS Quits Rate — Total Private",
        "subscore": LABOR,
        "frequency": "monthly",
        "units": "percent",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "leading",
        "is_scored": False,
        "backfill_start": "2001-01-01",
    },
    {
        "slug": "temphelps",
        "series_id": "TEMPHELPS",
        "source": "fred",
        "name": "Temporary Help Services Employment",
        "subscore": LABOR,
        "frequency": "monthly",
        "units": "thousands",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "leading",
        "is_scored": False,
        "backfill_start": "1990-01-01",
    },
    {
        "slug": "civpart",
        "series_id": "CIVPART",
        "source": "fred",
        "name": "Labor Force Participation Rate",
        "subscore": LABOR,
        "frequency": "monthly",
        "units": "percent",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "coincident",
        "is_scored": False,
        "backfill_start": "1990-01-01",
    },
    {
        "slug": "u6rate",
        "series_id": "U6RATE",
        "source": "fred",
        "name": "U-6 Unemployment (Total Underemployment)",
        "subscore": LABOR,
        "frequency": "monthly",
        "units": "percent",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "coincident",
        "is_scored": False,
        "backfill_start": "1994-01-01",
    },
    {
        "slug": "awhaetp",
        "series_id": "AWHAETP",
        "source": "fred",
        "name": "Average Weekly Hours — All Employees, Private",
        "subscore": LABOR,
        "frequency": "monthly",
        "units": "hours",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "leading",
        "is_scored": False,
        "backfill_start": "1990-01-01",
    },
    {
        "slug": "emratio",
        "series_id": "EMRATIO",
        "source": "fred",
        "name": "Employment-Population Ratio",
        "subscore": LABOR,
        "frequency": "monthly",
        "units": "percent",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "coincident",
        "is_scored": False,
        "backfill_start": "1990-01-01",
    },
    {
        "slug": "dspic96_raw",
        "series_id": "DSPIC96",
        "source": "fred",
        "name": "Real Disposable Personal Income (Level — supporting)",
        "subscore": BALANCE,
        "frequency": "monthly",
        "units": "billions of chained 2017 dollars",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "coincident",
        "is_scored": False,
        "backfill_start": "1990-01-01",
        "notes": "Raw level used to compute YoY for real_dpi_yoy indicator.",
    },
    {
        "slug": "dspi",
        "series_id": "DSPI",
        "source": "fred",
        "name": "Nominal Disposable Personal Income (Level — supporting)",
        "subscore": BALANCE,
        "frequency": "monthly",
        "units": "billions of dollars SAAR",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "coincident",
        "is_scored": False,
        "backfill_start": "1990-01-01",
        "notes": "Used as denominator for net worth / DPI ratio (×4 to annualize).",
    },
    {
        "slug": "bogz1fl192090005q",
        "series_id": "BOGZ1FL192090005Q",
        "source": "fred",
        "name": "Household & Nonprofit Net Worth (Level — supporting)",
        "subscore": BALANCE,
        "frequency": "quarterly",
        "units": "billions of dollars",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "lagging",
        "is_scored": False,
        "backfill_start": "1990-01-01",
        "notes": "Used as numerator for net worth / DPI ratio.",
    },
    {
        "slug": "drcclobs",
        "series_id": "DRCCLOBS",
        "source": "fred",
        "name": "Credit Card Delinquency Rate — Banks Not in Top 100 (Subprime Signal)",
        "subscore": CREDIT,
        "frequency": "quarterly",
        "units": "percent",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "leading",
        "is_scored": False,
        "backfill_start": "1990-01-01",
        "notes": (
            "Best public leading indicator for subprime credit stress. "
            "Provided the earliest warning signal in 2007 and again in 2023. "
            "Gets a prominent indicator library page even though it does not enter the headline score."
        ),
    },
    {
        "slug": "sahmcurrent",
        "series_id": "SAHMCURRENT",
        "source": "fred",
        "name": "Sahm Rule Recession Indicator",
        "subscore": LABOR,
        "frequency": "monthly",
        "units": "percentage points",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "coincident",
        "is_scored": False,
        "backfill_start": "1990-01-01",
        "notes": "Sahm ≥ 0.5 → recession signal. Context only; not scored.",
    },
    {
        "slug": "t10y3m",
        "series_id": "T10Y3M",
        "source": "fred",
        "name": "Yield Curve — 10Y minus 3M Treasury Spread",
        "subscore": CREDIT,
        "frequency": "daily",
        "units": "percentage points",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "leading",
        "is_scored": False,
        "backfill_start": "1990-01-01",
    },
    {
        "slug": "stlfsi4",
        "series_id": "STLFSI4",
        "source": "fred",
        "name": "St. Louis Fed Financial Stress Index",
        "subscore": CREDIT,
        "frequency": "weekly",
        "units": "index",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "leading",
        "is_scored": False,
        "backfill_start": "1994-01-01",
    },
    {
        "slug": "mspus",
        "series_id": "MSPUS",
        "source": "fred",
        "name": "Median Sales Price of Houses Sold (supporting — housing affordability)",
        "subscore": BIGTICKET,
        "frequency": "quarterly",
        "units": "dollars",
        "higher_is_better": False,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "coincident",
        "is_scored": False,
        "backfill_start": "1990-01-01",
        "notes": "Used as proxy median home price in housing affordability composite.",
    },
    {
        "slug": "mehoinusa672n",
        "series_id": "MEHOINUSA672N",
        "source": "fred",
        "name": "Median Household Income — US (supporting — housing affordability)",
        "subscore": BIGTICKET,
        "frequency": "monthly",
        "units": "dollars",
        "higher_is_better": True,
        "scoring_type": "percentile",
        "weight_in_subscore": 0.0,
        "lcl_class": "lagging",
        "is_scored": False,
        "backfill_start": "1990-01-01",
        "notes": (
            "Annual series interpolated to monthly for affordability composite. "
            "Census via FRED."
        ),
    },
]
# fmt: on


async def seed(client: libsql_client.Client) -> int:
    """Seed all indicators into the DB. Idempotent (slug UNIQUE → ON CONFLICT UPDATE)."""
    count = 0
    for ind in INDICATORS:
        await upsert_indicator(client, ind)
        count += 1
    log.info("seed_indicators.seed completed", count=count)
    return count

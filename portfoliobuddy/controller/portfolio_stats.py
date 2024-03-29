from portfoliobuddy.model import engine, session, Account, Trade, PriceOverride
import pandas as pd
import datetime
import yfinance as yf
from portfoliobuddy.configs import DEFAULT_CCY
from functools import partial
from quantstats.stats import volatility
import pandas_datareader.data as web
from portfoliobuddy.credentials import ALPHA_VANTAGE_TOKEN
from portfoliobuddy.controller import wrap_list


def get_trades(tickers=None, liquid_only=None, include_cash=True):
    db_session = session()
    query = db_session.query(Trade).join(Account)
    if tickers:
        query = query.filter(Trade.ticker.in_(tickers))
    if liquid_only is not None:
        query = query.filter(Account.is_liquid == bool(liquid_only))
    if not include_cash:
        query = query.filter(Trade.ticker != 'Cash')
    trades_df = pd.read_sql(query.statement, engine)
    return trades_df


def can_sell_trades(tickers=None, liquid_only=None):
    trades_df = get_trades(tickers=tickers, liquid_only=liquid_only, include_cash=False)
    trades_df = trades_df[['tradedate', 'ticker', 'account']]
    trades_df['today'] = datetime.date.today()
    trades_df['trade_age'] = (trades_df['today'] - trades_df['tradedate'])
    trades_df['trade_age'] = trades_df['trade_age'].apply(lambda trade_age: trade_age.days)
    trades_df['can_sell'] = trades_df['trade_age'] > 30
    trades_df['days_to_sell'] = trades_df['trade_age'].apply(lambda t_age: max(30 - t_age, 0))
    return trades_df


def get_px_overrides(tickers):
    db_session = session()
    query = db_session.query(PriceOverride).filter(PriceOverride.ticker.in_(tickers))
    px_override_df = pd.read_sql(query.statement, engine)
    return px_override_df


def get_last_close_yf(tickers):
    if len(tickers) == 1:
        yfticker = yf.Ticker(tickers[0])
        px_hist = yfticker.history(period='1d')
        px_hist['ticker'] = tickers[0]
        px_hist = px_hist[['ticker', 'Close']]
    else:
        yfticker = yf.Tickers(tickers)
        px_hist = yfticker.history(period='1d')
        px_hist = px_hist[['Close']]
        px_hist.columns = px_hist.columns.to_flat_index()
        px_hist.columns = [col[1] for col in px_hist.columns]
        px_hist = pd.melt(px_hist, value_vars=px_hist.columns)
        px_hist = px_hist.rename(columns={'variable': 'ticker', 'value': 'Close'})
    tickers_without_px = list(px_hist[px_hist['Close'].isna()]['ticker'].unique())
    if tickers_without_px:
        px_override_df = get_px_overrides(tickers_without_px)
        px_override_df = px_override_df.rename(columns={'px': 'Close'})
        px_override_df = px_override_df[['ticker', 'Close']]
        px_hist = px_hist[~px_hist['Close'].isna()]
        px_hist = pd.concat([px_hist, px_override_df])
    px_hist.loc[len(px_hist)] = ['Cash', 1]
    return px_hist


def get_last_close_av(tickers):
    px_hist = pd.DataFrame()
    for ticker in tickers:
        ticker_px = web.DataReader(ticker, 'av-daily', api_key=ALPHA_VANTAGE_TOKEN)
        ticker_px = ticker_px.reset_index()
        ticker_px = ticker_px.rename(columns={'close': 'Close'})
        ticker_px['ticker'] = ticker
        ticker_px = ticker_px[['ticker', 'Close']]
        if px_hist.empty:
            px_hist = ticker_px
        else:
            px_hist = pd.concat([px_hist, ticker_px])
    tickers_without_px = list(px_hist[px_hist['Close'].isna()]['ticker'].unique())
    if tickers_without_px:
        px_override_df = get_px_overrides(tickers_without_px)
        px_override_df = px_override_df.rename(columns={'px': 'Close'})
        px_override_df = px_override_df[['ticker', 'Close']]
        px_hist = px_hist[~px_hist['Close'].isna()]
        px_hist = pd.concat([px_hist, px_override_df])
    px_hist.loc[len(px_hist)] = ['Cash', 1]
    return px_hist


def strip_outlier_px(px_hist, price_col=None, zscore_threshold=10):
    outlier_cols = wrap_list(price_col) if price_col is not None else px_hist.columns
    # compute Z Score on all relevant price columns
    for col in outlier_cols:
        px_hist[f'{col}_zscore'] = abs((px_hist[col] - px_hist[col].mean()) / px_hist[col].std())

    # filter outlier values out using zscore_threshold
    for col in outlier_cols:
        px_hist = px_hist[px_hist[f'{col}_zscore'] < zscore_threshold]

    cols_to_include = [col for col in px_hist.columns if '_zscore' not in col]
    return px_hist[cols_to_include]


def convert_close_px(row, fx_rate_map):
    fx_rate = fx_rate_map[row['ccy']]
    adj_close = row['Close'] * fx_rate
    return adj_close


def convert_to_default_ccy(trades_df):
    ccys = trades_df['ccy'].unique()
    pairs_map = {f'{ccy}{DEFAULT_CCY}=X': ccy for ccy in ccys if ccy != DEFAULT_CCY}
    if len(pairs_map) == 1:
        pair = list(pairs_map.keys())[0]
        yf_ticker = yf.Ticker(pair)
        px_hist = yf_ticker.history(period='1d')
        fx_rate = px_hist['Close'][0]
        fx_rate_map = {pairs_map[pair]: fx_rate}
    else:
        yf_tickers = yf.Tickers(list(pairs_map.keys()))
        px_hist = yf_tickers.history(period='1d')
        px_hist = px_hist[['Close']]
        px_hist.columns = px_hist.columns.to_flat_index()
        px_hist.columns = [col[1] for col in px_hist.columns]
        px_hist = pd.melt(px_hist, value_vars=px_hist.columns)
        px_hist = px_hist.rename(columns={'variable': 'ticker', 'value': 'Close'})
        px_hist['ccy'] = px_hist['ticker'].apply(pairs_map.get)
        fx_rate_map = {row['ccy']: row['Close'] for row in px_hist.to_dict('records')}
    fx_rate_map[f'{DEFAULT_CCY}'] = 1
    convert_close_px_fn = partial(convert_close_px, fx_rate_map=fx_rate_map)
    trades_df['Close'] = trades_df.apply(convert_close_px_fn, axis=1)
    return trades_df


def get_close_value(tickers=None, liquid_only=None, incl_return_col=False, aggregate=False, in_default_ccy=True):
    trades_df = get_trades(tickers, liquid_only=liquid_only)
    tickers = list(trades_df['ticker'].unique())
    non_cash_tickers = [ticker for ticker in tickers if ticker.lower() != 'cash']
    last_close = get_last_close_yf(non_cash_tickers)
    trades_df = pd.merge(trades_df, last_close, how='left', on='ticker')
    # Scale GBp prices to GBP by dividing by 100
    gbp_pence_positions = trades_df['ccy'] == 'GBp'
    trades_df.loc[gbp_pence_positions, 'Close'] = trades_df['Close'] / 100
    trades_df.loc[gbp_pence_positions, 'ccy'] = 'GBP'
    if in_default_ccy:
        trades_df = convert_to_default_ccy(trades_df)
    trades_df['CloseValue'] = trades_df['Close'] * trades_df['qty']
    if aggregate:
        trades_df = trades_df.groupby(['ticker', 'idea', 'Close']).agg({
            'buy_cost': ['sum'],
            'qty': ['sum'],
            'CloseValue': ['sum'],
        })
        trades_df = trades_df.reset_index()
        trades_df.columns = ['ticker', 'idea', 'Close', 'buy_cost', 'qty', 'CloseValue']
    if incl_return_col:
        trades_df['ReturnPct'] = (trades_df['CloseValue'] - trades_df['buy_cost']) / trades_df['buy_cost']
    return trades_df


def asset_conc(tickers=None, idea_mode=None, liquid_only=None, in_default_ccy=True):
    close_val_df = get_close_value(tickers, liquid_only, in_default_ccy)
    if idea_mode:
        close_val_df = close_val_df.groupby(['idea']).agg({'CloseValue': ['sum']})
        close_val_df = close_val_df.rename(columns={'idea': 'ticker'})
    else:
        close_val_df = close_val_df.groupby(['ticker']).agg({'CloseValue': ['sum']})
    close_val_df = close_val_df.reset_index()
    close_val_df.columns = ['ticker', 'CloseValue']
    close_val_df['TotalValue'] = close_val_df['CloseValue'].sum()
    close_val_df['concentration'] = close_val_df['CloseValue'] / close_val_df['TotalValue']
    close_val_df = close_val_df[['ticker', 'concentration']]
    close_val_df = close_val_df.sort_values(by='concentration', ascending=False)
    return close_val_df


def get_ticker_volatility(ticker, period):
    yft = yf.Ticker(ticker)
    px_hist = yft.history(period='max')
    if not px_hist.empty:
        px_hist_clean = strip_outlier_px(px_hist, 'Close')
        vol_stats = volatility(px_hist_clean, periods=period)
        vol = vol_stats['Close']
        return vol


def get_position_size_and_vol_in_name(ticker, period, loss_threshold_pct, liquid_only=False):
    close_val_df = get_close_value(liquid_only=liquid_only)
    portfolio_val = close_val_df['CloseValue'].sum()
    vol = get_ticker_volatility(ticker, period)
    loss_threshold = loss_threshold_pct * portfolio_val
    position_size = loss_threshold / vol
    return position_size, vol

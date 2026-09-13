"""Reproducible descriptive EDA; no predictions, API calls, or input mutations."""
from pathlib import Path
import calendar
import hashlib
import json
import platform
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from IPython.display import Image, Markdown, display

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'eda_outputs'
plt.rcParams.update({'figure.figsize': (11, 5), 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False, 'savefig.dpi': 140})


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def next_month(date, day):
    year, month = date.year + (date.month == 12), date.month % 12 + 1
    return pd.Timestamp(year=year, month=month, day=min(day, calendar.monthrange(year, month)[1]))


class Analysis:
    def __init__(self):
        OUT.mkdir(exist_ok=True)
        (OUT / 'figures').mkdir(exist_ok=True)
        (OUT / 'tables').mkdir(exist_ok=True)
        self.t = {p.stem: pd.read_csv(p, keep_default_na=False) for p in sorted((ROOT / 'dataset').glob('*.csv'))}
        self.summary = {'schema_version': '1.0', 'population': {
            'request_analyses': '250 evaluation requests; no sample output labels used',
            'supporting_analyses': 'evaluation users unless explicitly labeled all supplied rows',
            'recurrence': 'settled cash events with settlement date strictly before request date',
            'evidence': 'metadata and provisional keyword triage; not normalized facts'},
            'input_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted((ROOT / 'dataset').rglob('*')) if p.is_file()},
            'versions': {'python': platform.python_version(), 'pandas': pd.__version__,
                         'numpy': np.__version__, 'matplotlib': matplotlib.__version__},
            'sections': {}, 'artifacts': []}
        self.r = self.t['requests'].merge(self.t['financial_profiles'], on='user_id', validate='one_to_one')
        for c in ['requested_amount', 'current_available_balance', 'minimum_balance_to_keep']:
            self.r[c] = pd.to_numeric(self.r[c])
        self.r['headroom'] = self.r.current_available_balance - self.r.minimum_balance_to_keep
        self.r['amount_headroom_ratio'] = self.r.requested_amount.div(self.r.headroom.where(self.r.headroom > 0))
        self.r['deadline_days'] = (pd.to_datetime(self.r.desired_completion_date) - pd.to_datetime(self.r.request_date)).dt.days
        self.e = self.t['financial_events'].merge(self.r[['user_id', 'request_date', 'home_currency']], on='user_id', validate='many_to_one')
        self.e['amount'] = pd.to_numeric(self.e.amount, errors='coerce')
        self.e['date'] = pd.to_datetime(self.e.settlement_date, errors='coerce')
        self.e['foreign'] = self.e.currency != self.e.home_currency
        self.m = self.t['messages'][self.t['messages'].user_id.isin(self.r.user_id)].copy()
        self.i = self.t['images'][self.t['images'].user_id.isin(self.r.user_id)].copy()
        self.o = self.t['request_payment_options'].merge(self.r[['request_id','requested_amount','desired_completion_date','payment_methods_user_will_consider','max_installment_months']], on='request_id', validate='many_to_one')
        self.report_parts = ['# Executed EDA findings\n\nGenerated from `plan/eda.ipynb`. See the notebook for analysis code and charts.']

    def table(self, name, frame):
        if isinstance(frame, pd.Series):
            frame = frame.rename('count').rename_axis('value').reset_index()
        frame.to_csv(OUT / 'tables' / f'{name}.csv', index=False)
        self.summary['artifacts'].append(f'eda_outputs/tables/{name}.csv')
        display(frame.head(15))
        if len(frame) > 15:
            display(Markdown(f'Showing 15 of {len(frame)} rows; complete table: `eda_outputs/tables/{name}.csv`.'))

    def plot(self, name, fig):
        fig.tight_layout()
        path = OUT / 'figures' / f'{name}.png'
        fig.savefig(path)
        display(Image(filename=str(path)))
        plt.close(fig)
        self.summary['artifacts'].append(f'eda_outputs/figures/{name}.png')

    def findings(self, key, stats, observations, implications):
        assert 2 <= len(observations) <= 5
        self.summary['sections'][key] = clean({'statistics': stats, 'observations': observations, 'implications': implications})
        text = '### Extract Insights\n\n' + '\n'.join('- ' + s for s in observations)
        text += '\n\n### Implications for the Financial Agent\n\n' + '\n'.join(f'- **{tag}:** {s}' for tag, s in implications)
        display(Markdown(text))
        self.report_parts.append('## ' + key.replace('_', ' ').title() + '\n\n' + text)

    def bar(self, ax, series, title, xlabel='Count', ylabel='Category'):
        series.sort_values().plot.barh(ax=ax, color='#287b8e')
        ax.set(title=title, xlabel=xlabel, ylabel=ylabel)

    def integrity(self):
        keys = {'financial_profiles':'user_id','financial_events':'event_id','requests':'request_id',
                'sample_requests':'request_id','messages':'message_id','images':'image_id','request_payment_options':'payment_option_id'}
        rows = pd.DataFrame([{'table': k, 'rows': len(v), 'blank_cells': int(v.eq('').sum().sum()),
                              'duplicate_primary_ids': int(v[keys[k]].duplicated().sum()) if k in keys else None} for k,v in self.t.items()])
        allreq = pd.concat([self.t['requests'],self.t['sample_requests']],ignore_index=True)
        refs = {'user_id':set(self.t['financial_profiles'].user_id), 'request_id':set(allreq.request_id),
                'related_event_id':set(self.t['financial_events'].event_id), 'linked_event_id':set(self.t['financial_events'].event_id)}
        orphans = {k: sum(int((~v[k].isin(target) & v[k].ne('')).sum()) for v in self.t.values() if k in v) for k,target in refs.items()}
        image_exists = self.t['images'].image_id.map(lambda x:(ROOT/'dataset/media/images'/f'{x}.png').is_file())
        missing = self.t['financial_events'].query("amount == ''")
        covered = missing.event_id.isin(self.t['images'].loc[image_exists,'related_event_id'])
        self.table('inventory', rows)
        self.findings('integrity', {'inventory':rows.to_dict('records'),'orphans':orphans,'missing_amounts':len(missing),'missing_amounts_with_image':int(covered.sum()),
            'output_ids_match':set(self.t['output'].request_id)==set(self.t['requests'].request_id)}, [
            f"The supplied tables contain {len(self.r)} evaluation requests and {len(self.t['sample_requests'])} samples; evaluation users contribute {len(self.e):,} events.",
            f"Primary-key duplicates total {int(rows.duplicate_primary_ids.sum())}; unresolved populated foreign keys total {sum(orphans.values())}.",
            f"All-data missing amounts: {len(missing)}; {int(covered.sum())} have existing linked PNGs. Their numeric values are still unextracted."],
            [('DETERMINISTIC','Validate identifiers, typed fields and joins at ingestion; preserve blank amounts until evidence resolves them.'),
             ('LLM_REQUIRED','Image-backed amounts need visual interpretation, including selecting the correct monetary field; EDA does not prove an LLM is the only possible extractor.')])

    def requests(self):
        c = self.r.request_type.value_counts(); fig,ax=plt.subplots(); self.bar(ax,c,'Evaluation requests by type','Requests','Request type'); self.plot('01_request_types',fig)
        self.findings('request_types', c.to_dict(), [f"Type counts range from {c.min()} to {c.max()} across {len(c)} types.",
            f"Largest type share is {c.max()/len(self.r):.1%}; no single type dominates the evaluation population."], [('DETERMINISTIC','Apply the same safety constraints across all request types; do not infer affordability from type frequency.')])
        fig,axes=plt.subplots(1,2,figsize=(13,5)); currencies=sorted(self.r.home_currency.unique())
        axes[0].boxplot([self.r.loc[self.r.home_currency.eq(c),'requested_amount'] for c in currencies],tick_labels=currencies)
        axes[0].set(yscale='log',title='Requested amounts by home currency',xlabel='Home currency (amounts not comparable across groups)',ylabel='Requested amount (native units; log scale)')
        ratio=self.r.amount_headroom_ratio.dropna(); axes[1].hist(ratio,bins=25,color='#287b8e'); axes[1].set(title='Request amount / positive raw headroom',xlabel='Ratio (1 = entire raw headroom)',ylabel='Requests'); self.plot('02_amount_headroom',fig)
        monetary=self.r.groupby('home_currency').requested_amount.agg(['count','min','median','max']).reset_index(); self.table('amounts_by_currency',monetary)
        self.findings('amount_headroom',{'by_currency':monetary.to_dict('records'),'nonpositive_headroom':int(self.r.headroom.le(0).sum()),'ratio_quantiles':ratio.quantile([.25,.5,.75,.95,1]).to_dict(),'ratio_above_one':int(ratio.gt(1).sum())},[
            f"{len(ratio)} requests have positive headroom; {self.r.headroom.le(0).sum()} have zero or negative headroom and are excluded from ratios.",
            f"{ratio.gt(1).sum()}/{len(ratio)} ({ratio.gt(1).mean():.1%}) requests exceed raw headroom; median ratio is {ratio.median():.2f}, 95th percentile {ratio.quantile(.95):.2f}.",
            f"Within-currency max/median request ratios range from {(monetary['max']/monetary['median']).min():.1f} to {(monetary['max']/monetary['median']).max():.1f}."],
            [('DETERMINISTIC','Raw headroom is balance minus reserve, not 90-day safe spending. Do not mix native currency amounts.'),('STATISTICAL','Forecast commitments before interpreting a low request/headroom ratio as affordable.')])
        d=self.r.deadline_days; fig,ax=plt.subplots(); ax.hist(d,bins=np.arange(0,101,7),color='#287b8e'); ax.set(title='Time available to complete evaluation requests',xlabel='Days from request to deadline',ylabel='Requests'); self.plot('03_deadlines',fig)
        self.findings('deadlines',{'quantiles':d.quantile([0,.25,.5,.75,1]).to_dict(),'within_30_days':int(d.le(30).sum())},[
            f"Deadlines span {d.min()}–{d.max()} days; median {d.median():.0f} days.",f"{d.le(30).sum()}/{len(d)} ({d.le(30).mean():.1%}) must complete within 30 days."], [('DETERMINISTIC','Compute completion eligibility using each request date and deadline, independently of the forecast horizon.')])

    def preferences(self):
        c=self.r.payment_methods_user_will_consider.value_counts(); fig,ax=plt.subplots(figsize=(12,5)); self.bar(ax,c,'Accepted payment-method combinations','Users','Accepted methods'); self.plot('04_preferences',fig)
        count=self.r.payment_methods_user_will_consider.str.split('|').str.len()
        self.findings('preferences',{'combinations':c.to_dict(),'single_method':int(count.eq(1).sum()),'restricted':int(count.lt(3).sum()),'partial_allowed':int(self.r.allows_partial_payment.astype(str).str.lower().eq('true').sum())},[
            f"{count.lt(3).sum()}/{len(count)} ({count.lt(3).mean():.1%}) users exclude at least one immediate payment method.",
            f"{count.eq(1).sum()} users ({count.eq(1).mean():.1%}) accept exactly one method; request-level partial permission still needs a separate check."], [('DETERMINISTIC','Filter payment candidates by user acceptance before ranking; financial capacity and method eligibility are distinct.')])

    def events(self):
        c=self.e.status.value_counts(); fig,axes=plt.subplots(1,2,figsize=(12,5)); self.bar(axes[0],c,'Event status: full population','Events','Status'); self.bar(axes[1],c.drop('settled',errors='ignore'),'Event status: non-settled detail','Events','Status'); self.plot('05_status',fig)
        self.findings('event_status',c.to_dict(),[f"Settled rows account for {c.get('settled',0)}/{len(self.e)} ({c.get('settled',0)/len(self.e):.1%}) evaluation-user events.",f"There are {c.get('pending',0)} pending and {c.get('unrealized',0)} unrealized rows; these require different cash treatment despite their small frequency."], [('DETERMINISTIC','Reserve pending debits; exclude pending credits and unrealized value from available cash. Failed collections may leave liabilities.')])
        types=self.e.event_type.value_counts(); cats=self.e.category.value_counts(); fig,axes=plt.subplots(1,2,figsize=(13,6)); self.bar(axes[0],types,'Financial-event types','Events','Type'); self.bar(axes[1],cats.head(10),'Ten most frequent event categories','Events','Category'); self.plot('06_event_types',fig)
        self.findings('event_types',{'types':types.to_dict(),'categories':cats.to_dict()},[f"{types.index[0]} contributes {types.iloc[0]:,} events ({types.iloc[0]/len(self.e):.1%}).",f"The three most frequent categories ({', '.join(cats.head(3).index)}) account for {cats.head(3).sum()/len(self.e):.1%} of events."], [('STATISTICAL','Recurring obligations and essential variable spending need separate estimation; an expense type does not establish recurrence.')])
        n=self.e.groupby('user_id').size().reindex(self.r.user_id,fill_value=0); fig,ax=plt.subplots(); ax.hist(n,bins=20,color='#287b8e'); ax.set(title='Historical and supplied event counts per evaluation user',xlabel='Event rows per user',ylabel='Users'); self.plot('07_event_counts',fig)
        self.findings('event_counts',n.describe().to_dict(),[f"Users have {n.min()}–{n.max()} rows; median {n.median():.0f}, IQR {n.quantile(.25):.0f}–{n.quantile(.75):.0f}.",f"{n.gt(n.quantile(.75)).sum()} users exceed the upper-quartile event count; row volume alone is not evidence complexity."], [('DETERMINISTIC','Retrieve and process per-user histories; avoid sending the entire event table to an interpreter.')])

    def recurrence(self):
        h=self.e.loc[self.e.status.eq('settled') & self.e.direction.isin(['credit','debit']) & self.e.date.lt(pd.to_datetime(self.e.request_date))].copy()
        h['normalized_description']=h.description.str.lower().str.replace(r'\s+',' ',regex=True).str.strip()
        keys=['user_id','normalized_description','category','direction','currency']
        series=[]; errors=[]; intervals=[]
        for sid,(key,g) in enumerate(h.groupby(keys,sort=True)):
            # Same-day rows are aggregated within the declared series; all-missing days stay missing.
            daily=g.groupby('date').amount.sum(min_count=1).sort_index()
            if len(daily)<4: continue
            dates=daily.index; values=daily.to_numpy(dtype=float); gaps=np.diff(dates.values).astype('timedelta64[D]').astype(float)
            cv=np.nanstd(values)/abs(np.nanmean(values)) if np.nanmean(values)!=0 else np.nan
            median_gap=float(np.median(gaps)); iqr=float(np.quantile(gaps,.75)-np.quantile(gaps,.25))
            cadence='monthly_candidate' if 28<=median_gap<=32 else 'weekly_candidate' if 6<=median_gap<=8 else 'other'
            series.append(dict(series_id=sid,user_id=key[0],description=key[1],category=key[2],direction=key[3],currency=key[4],n_dates=len(dates),median_gap=median_gap,interval_iqr=iqr,amount_cv=cv,cadence=cadence))
            intervals.extend(gaps.tolist())
            for cut in range(3,len(dates)):
                train=dates[:cut]; actual=dates[cut]; prior=values[:cut]; target=values[cut]
                delta=np.diff(train.values).astype('timedelta64[D]').astype(float)
                day=int(pd.Series(train.day).mode().iloc[0])
                preds={'fixed_30':train[-1]+pd.Timedelta(days=30),'last_interval':train[-1]+pd.Timedelta(days=float(delta[-1])),
                       'median_interval':train[-1]+pd.Timedelta(days=float(np.median(delta))),'calendar_month':next_month(train[-1],day)}
                assert max(train)<actual and actual<pd.Timestamp(g.request_date.iloc[0])
                for method,pred in preds.items():
                    errors.append(dict(series_id=sid,kind='date',method=method,error=abs((pred-actual).total_seconds())/86400,direction=key[3],cadence=cadence,train_end=str(train[-1].date()),target_date=str(actual.date()),request_date=g.request_date.iloc[0]))
                scale=np.nanmedian(abs(prior))
                if np.isfinite(target) and np.isfinite(scale) and scale>0:
                    for method,pred in {'last_amount':prior[-1],'historical_median':np.nanmedian(prior),'trailing_3_median':np.nanmedian(prior[-3:])}.items():
                        if np.isfinite(pred):errors.append(dict(series_id=sid,kind='amount',method=method,error=abs(pred-target)/scale,direction=key[3],cadence=cadence,train_end=str(train[-1].date()),target_date=str(actual.date()),request_date=g.request_date.iloc[0]))
        self.series=pd.DataFrame(series); self.errors=pd.DataFrame(errors)
        self.series.to_csv(OUT/'tables/recurring_candidates.csv',index=False); self.errors.to_csv(OUT/'tables/recurrence_folds.csv',index=False)
        s=self.series; fig,axes=plt.subplots(1,2,figsize=(12,5)); axes[0].hist(intervals,bins=35,color='#287b8e'); axes[0].set(title='All candidate-series observed intervals',xlabel='Days between distinct settlement dates',ylabel='Intervals'); axes[1].hist(s.median_gap,bins=30,color='#287b8e'); axes[1].set(title='Median interval per candidate series',xlabel='Median interval (days)',ylabel='Series'); self.plot('08_recurrence_intervals',fig)
        self.findings('recurrence_intervals',{'series':len(s),'users':s.user_id.nunique(),'intervals':len(intervals),'monthly_share':s.median_gap.between(28,32).mean(),'weekly_share':s.median_gap.between(6,8).mean(),'iqr_above_7_share':s.interval_iqr.gt(7).mean(),'same_day_aggregation':'sum within exact grouping, min_count=1'},[
            f"{len(s)} candidates across {s.user_id.nunique()} users have at least four distinct historical settlement dates.",f"{s.median_gap.between(28,32).mean():.1%} have median intervals of 28–32 days; {s.median_gap.between(6,8).mean():.1%} fall in 6–8 days.",f"{s.interval_iqr.gt(7).sum()} ({s.interval_iqr.gt(7).mean():.1%}) have interval IQR above seven days; description-based groups are candidates, not confirmed obligations."], [('STATISTICAL','Infer cadence from history rather than assuming 30 days. Different descriptions may fragment one obligation; one description may merge multiple obligations.')])
        fig,ax=plt.subplots(); ax.hist(s.amount_cv.dropna(),bins=35,color='#287b8e'); ax.set(title='Amount variability within recurring candidates',xlabel='Coefficient of variation (std / absolute mean)',ylabel='Series'); self.plot('09_amount_variability',fig)
        self.findings('recurrence_amounts',{'cv_quantiles':s.amount_cv.quantile([0,.5,.75,.95,1]).to_dict(),'cv_above_025':int(s.amount_cv.gt(.25).sum()),'cv_missing':int(s.amount_cv.isna().sum())},[f"Median amount CV is {s.amount_cv.median():.3f}; 95th percentile is {s.amount_cv.quantile(.95):.3f}.",f"{s.amount_cv.gt(.25).sum()}/{s.amount_cv.notna().sum()} measurable series exceed CV 0.25; {s.amount_cv.isna().sum()} have undefined CV."], [('STATISTICAL','Compare robust amount estimators; point-error accuracy alone does not guarantee conservative expense reserves.')])
        # Equal series weighting, after averaging each series' expanding-window folds.
        per=self.errors.groupby(['kind','method','series_id','direction','cadence'],as_index=False).error.mean()
        metrics=per.groupby(['kind','method'],as_index=False).agg(mean_error=('error','mean'),series=('series_id','nunique'))
        metrics=metrics.merge(self.errors.groupby(['kind','method']).size().rename('folds').reset_index(),on=['kind','method'])
        self.metrics=metrics; self.table('recurrence_method_errors',metrics)
        breakdown=per.groupby(['kind','method','direction','cadence'],as_index=False).agg(mean_error=('error','mean'),series=('series_id','nunique')); breakdown.to_csv(OUT/'tables/recurrence_error_breakdown.csv',index=False)
        fig,axes=plt.subplots(1,2,figsize=(13,5))
        for ax,kind,unit in zip(axes,['date','amount'],['MAE (days; equal series weighting)','Absolute error / training median absolute amount']):
            z=metrics[metrics.kind.eq(kind)].set_index('method').mean_error; self.bar(ax,z,f'Next-{kind} forecast errors',unit,'Heuristic')
        self.plot('10_forecast_errors',fig)
        winners={k:metrics.loc[metrics.kind.eq(k)].sort_values(['mean_error','method']).iloc[0].to_dict() for k in ['date','amount']}; self.winners=winners
        ties={k:metrics.loc[metrics.kind.eq(k)&np.isclose(metrics.mean_error,winners[k]['mean_error'],rtol=1e-6,atol=1e-9),'method'].tolist() for k in winners}
        self.findings('recurrence_backtest',{'metrics':metrics.to_dict('records'),'breakdown':breakdown.to_dict('records'),'winners':winners,'ties':ties,'weighting':'mean within series, then mean across series','split':'expanding window; three prior dates minimum; strictly before request','amount_scale':'training median absolute amount'},[
            f"Lowest date error: {', '.join(ties['date'])}, {winners['date']['mean_error']:.3f} days across {winners['date']['series']} series and {winners['date']['folds']} folds.",
            f"Lowest amount error: {', '.join(ties['amount'])}, {winners['amount']['mean_error']:.4f} normalized absolute error across {winners['amount']['series']} series.",
            f"The benchmark contains {len(self.errors):,} method/fold rows; missing target or unscalable amount folds are excluded. These are one-step historical results, not a 90-day safety evaluation."], [('STATISTICAL','Use the observed winners as baselines and inspect direction/cadence breakdowns; validate conservative tails and regime changes separately.'),('EDGE_CASE','Calendar months clip at month end; fixed-day and calendar-month recurrence are distinct. Ties are reported, not hidden.')])

    def options(self):
        o=self.o.copy(); inst=o[o.payment_method.eq('installments')]; fig,axes=plt.subplots(1,2,figsize=(12,5)); self.bar(axes[0],inst.payment_frequency_days.astype(str).value_counts(),'Installment intervals','Offers','Days'); self.bar(axes[1],inst.number_of_payments.value_counts().sort_index(),'Installment payment counts','Offers','Number of payments'); self.plot('11_payment_options',fig)
        num=lambda c:pd.to_numeric(o[c],errors='coerce')
        # Relative tolerance is inappropriate for large currency amounts; use one hundredth native unit.
        o['arithmetic_bad']=(num('payment_amount')*num('number_of_payments')-num('total_payable_amount')).abs().gt(.01)
        o['fee_bad']=(num('total_payable_amount')-o.requested_amount-num('financing_fee')).abs().gt(.01)
        days=num('payment_frequency_days').fillna(0)*(num('number_of_payments')-1)
        o['last_payment_date']=pd.to_datetime(o.first_payment_date)+pd.to_timedelta(days,unit='D')
        o['after_deadline']=o.last_payment_date.gt(pd.to_datetime(o.desired_completion_date))
        o['method_rejected']=[m not in a.split('|') for m,a in zip(o.payment_method,o.payment_methods_user_will_consider)]
        o['above_installment_count_limit']=o.payment_method.eq('installments') & (num('number_of_payments').gt(num('max_installment_months')) | num('max_installment_months').isna())
        checks={k:int(o[k].sum()) for k in ['arithmetic_bad','fee_bad','after_deadline','method_rejected','above_installment_count_limit']}
        self.table('option_validation',o[['payment_option_id','request_id','last_payment_date']+list(checks)])
        self.findings('payment_options',{'offers':len(o),'installments':len(inst),'intervals':inst.payment_frequency_days.value_counts().to_dict(),'checks':checks,'installment_limit_screen':'number_of_payments > max_installment_months; count-based EDA screen, not final month-duration interpretation'},[
            f"Evaluation requests have {len(o)} offers, including {len(inst)} installment offers using {sorted(inst.payment_frequency_days.astype(str).unique())}-day intervals.",f"Arithmetic discrepancies above 0.01 native units: {checks['arithmetic_bad']} payment-total and {checks['fee_bad']} principal-plus-fee mismatches.",f"{checks['after_deadline']} offers finish after the deadline; {checks['method_rejected']} use rejected methods; {checks['above_installment_count_limit']} fail the declared installment-count screen. These flags can overlap."], [('DETERMINISTIC','Calculate schedules with exact day intervals and validate fees, deadlines and preferences before cash simulation.'),('EDGE_CASE','The month-limit screen uses payment count; resolve duration interpretation explicitly before implementing final eligibility.')])

    def evidence(self):
        patterns={'payroll':r'salary|payroll|gaji|penggajian|employer|stipend',
            'timing_change':r'delay|revised|reschedul|later|replaces|ditunda|tertunda',
            'amount_change':r'increase|raise|reduc|renew|naik|berubah',
            'cancellation_or_end':r'cancel|ended|terminated|off-season|berakhir',
            'unsettled_or_dispute':r'pending|disput|investigat|not.*posted|awaiting|failed|retry|retried',
            'transfer_or_multiple_accounts':r'transfer|two.*accounts|separate.*accounts|two.*card'}
        tags=pd.DataFrame({k:self.m.message_text.str.contains(v,case=False,regex=True,na=False) for k,v in patterns.items()})
        tags['unmatched']=~tags.any(axis=1); counts=tags.sum().sort_values(ascending=False)
        self.table('message_taxonomy',counts); self.m.assign(**{c:tags[c] for c in tags})[['message_id','user_id']+list(tags)].to_csv(OUT/'tables/message_topic_flags.csv',index=False)
        future=pd.to_datetime(self.m.sent_at,utc=True).dt.date > pd.to_datetime(self.m.user_id.map(self.r.set_index('user_id').request_date)).dt.date
        self.findings('message_triage',{'messages':len(self.m),'users':self.m.user_id.nunique(),'topic_counts':counts.to_dict(),'patterns':patterns,'multi_topic':int(tags.drop(columns='unmatched').sum(axis=1).gt(1).sum()),'after_request_day':int(future.sum()),'blank_event_links':int(self.m.related_event_id.eq('').sum()),'reviewed_images':{'image_01':'payslip: earnings/deductions/net pay','image_03':'retail receipt: item lines/net/cash paid','image_09':'utility receipt: charge/due dates and received amount'},'image_review_scope':'three representative images across all supplied users; personal details not transcribed'},[
            f"{len(self.m)} evaluation-user messages cover {self.m.user_id.nunique()}/{len(self.r)} users; {self.m.related_event_id.eq('').sum()} lack a direct event link.",f"{int(tags.unmatched.sum())} messages are unmatched by the provisional multilingual patterns; {int(tags.drop(columns='unmatched').sum(axis=1).gt(1).sum())} match multiple topics, which does not itself prove conflict.",f"{future.sum()} messages are dated after their request day; same-day availability cannot be resolved from a date-only request.",f"Three representative supplied images were visually inspected: a payslip, retail receipt, and utility receipt. EDA leaves all {self.e.amount.isna().sum()} evaluation-user missing amounts unresolved."], [('LLM_REQUIRED','Interpret amendments, effective dates and document-specific amounts with provenance; keyword triage is not fact extraction or proof of model necessity.'),('EDGE_CASE','Filter evidence by availability; request dates lack time-of-day precision. Never export raw personal document details.')])
        all_e=self.t['financial_events'].set_index('event_id'); links=self.e[self.e.linked_event_id.ne('')].copy(); links['previous_status']=links.linked_event_id.map(all_e.status); trans=links.groupby(['previous_status','status']).size().rename('rows').reset_index(); self.table('lifecycle_transitions',trans)
        self.findings('lifecycle',{'linked_rows':len(links),'users':links.user_id.nunique(),'transitions':trans.to_dict('records')},[f"{len(links)} linked evaluation-user rows occur across {links.user_id.nunique()} users ({links.user_id.nunique()/len(self.r):.1%}).",f"The links exhibit {len(trans)} status-transition combinations; transitions describe row relationships, not resolved duplicates or cash obligations."], [('DETERMINISTIC','Traverse lifecycle links and retain cash status rather than dropping all linked rows.'),('LLM_REQUIRED','Use evidence to distinguish cancellation, settlement, transfers and still-outstanding liabilities when structured rows are insufficient.')])

    def fx_pending(self):
        rates=self.t['exchange_rates']; duplicate=int(rates.duplicated(['rate_date','from_currency','to_currency']).sum())
        lookup=rates.drop_duplicates(['rate_date','from_currency','to_currency']).set_index(['rate_date','from_currency','to_currency']).rate.to_dict()
        e=self.e.copy(); e['fx_rate']=[1.0 if not foreign else lookup.get((date,cur,home),np.nan) for foreign,date,cur,home in zip(e.foreign,e.settlement_date,e.currency,e.home_currency)]
        e['home_amount']=e.amount*pd.to_numeric(e.fx_rate,errors='coerce'); self.converted=e
        foreign=e[e.foreign & e.direction.isin(['credit','debit'])]; missing=foreign[foreign.fx_rate.isna()]
        self.table('fx_missing',missing[['event_id','user_id','settlement_date','currency','home_currency','status']])
        self.findings('fx_coverage',{'rate_rows':len(rates),'duplicate_keys':duplicate,'foreign_cash_rows':len(foreign),'missing_rate_rows':len(missing),'missing_event_ids':missing.event_id.tolist()},[f"{len(foreign)} evaluation-user cash rows need conversion; {len(missing)} lack the exact dated directed rate.",f"The {len(rates)} supplied rates contain {duplicate} duplicate date/pair keys; no inverse, nearest-date, or live-rate fallback is applied."], [('DETERMINISTIC','Use settlement-date directed rates; fail explicitly on unresolved required conversions rather than inventing rates.')])
        pending=e[e.status.eq('pending')&e.direction.eq('debit')]; totals=pending.groupby('user_id').home_amount.sum(min_count=1)
        unknown=set(pending.loc[pending.home_amount.isna(),'user_id']); complete=totals.drop(index=list(unknown),errors='ignore'); head=self.r.set_index('user_id').headroom
        ratios=complete/head.reindex(complete.index).where(head.reindex(complete.index)>0)
        self.table('pending_debit_reserves',pd.DataFrame({'home_currency':self.r.set_index('user_id').home_currency.reindex(totals.index),'known_sum':totals,'incomplete':totals.index.isin(unknown),'ratio_to_positive_headroom':ratios}).reset_index())
        self.findings('pending_debits',{'rows':len(pending),'users':pending.user_id.nunique(),'incomplete_users':len(unknown),'ratio_quantiles':ratios.quantile([.5,.9,1]).to_dict(),'ratio_above_025':int(ratios.gt(.25).sum()),'nonpositive_headroom_users':int(head.reindex(complete.index).le(0).sum())},[f"{len(pending)} pending debit rows affect {pending.user_id.nunique()} users; {len(unknown)} users have incomplete amount/conversion evidence.",f"Among {ratios.notna().sum()} complete positive-headroom users, median pending debit/headroom is {ratios.median():.1%}; {ratios.gt(.25).sum()} exceed 25%."], [('DETERMINISTIC','Reserve pending debits per user; keep unknown reserves explicit rather than treating missing amounts as zero.')])

    def complexity(self):
        r=self.r.set_index('user_id'); s=self.series
        flags=pd.DataFrame(index=r.index)
        flags['messages']=flags.index.isin(self.m.user_id)
        flags['images_or_missing']=flags.index.isin(self.i.user_id)|flags.index.isin(self.e.loc[self.e.amount.isna(),'user_id'])
        flags['lifecycle']=flags.index.isin(self.e.loc[self.e.linked_event_id.ne(''),'user_id'])
        flags['foreign_currency']=flags.index.isin(self.e.loc[self.e.foreign,'user_id'])
        flags['pending_debit']=flags.index.isin(self.e.loc[self.e.status.eq('pending')&self.e.direction.eq('debit'),'user_id'])
        flags['restricted_methods']=r.payment_methods_user_will_consider.str.split('|').str.len().lt(3)
        flags['irregular_recurrence']=flags.index.isin(s.loc[s.interval_iqr.gt(7)|s.amount_cv.gt(.25),'user_id'])
        evidence=flags[['messages','images_or_missing','lifecycle']].any(axis=1); score=flags.sum(axis=1)
        tiers=pd.Series(np.where(evidence,'evidence-heavy',np.where(score<=1,'simple','structured-complex')),index=flags.index)
        result=flags.astype(int).assign(score=score,tier=tiers,request_id=r.request_id,event_count=self.e.groupby('user_id').size()).reset_index()
        self.complexity_table=result; self.table('request_complexity',result)
        counts=tiers.value_counts(); components=flags.sum().sort_values(ascending=False); fig,axes=plt.subplots(1,2,figsize=(13,5)); self.bar(axes[0],counts,'Request complexity: declared triage tiers','Requests','Tier'); self.bar(axes[1],components,'Prevalence of score components','Requests with flag','Component'); self.plot('12_complexity',fig)
        sensitivity=[]
        for gap,cv in [(5,.15),(7,.25),(10,.4)]:
            f=flags.copy(); f['irregular_recurrence']=f.index.isin(s.loc[s.interval_iqr.gt(gap)|s.amount_cv.gt(cv),'user_id']); sc=f.sum(axis=1)
            sensitivity.append({'interval_iqr_threshold':gap,'cv_threshold':cv,'simple':int((~evidence&sc.le(1)).sum()),'evidence_heavy':int(evidence.sum()),'structured_complex':int((~evidence&sc.gt(1)).sum()),'mean_score':sc.mean()})
        self.findings('complexity',{'tiers':counts.to_dict(),'components':components.to_dict(),'score_quantiles':score.quantile([0,.5,.75,1]).to_dict(),'sensitivity':sensitivity,'definitions':{'score':'one point per listed boolean flag','simple':'no evidence flags and score <= 1','evidence-heavy':'message, image/missing amount, or lifecycle flag','structured-complex':'remaining requests','irregular':'interval IQR > 7 days OR amount CV > 0.25'},'highest_requests':result.sort_values(['score','event_count','request_id'],ascending=[False,False,True]).head(10).to_dict('records')},[
            f"Under the declared rule: {counts.get('simple',0)} simple, {counts.get('structured-complex',0)} structured-complex, and {counts.get('evidence-heavy',0)} evidence-heavy evaluation requests.",f"Most prevalent score components: {components.index[0]} ({components.iloc[0]}/{len(r)}) and {components.index[1]} ({components.iloc[1]}/{len(r)}). These are score contributions, not independently validated difficulty predictors.",f"Across three irregularity thresholds, simple counts range {min(x['simple'] for x in sensitivity)}–{max(x['simple'] for x in sensitivity)}; evidence-heavy counts remain fixed by definition."], [('DETERMINISTIC','Use explicit flags for routing and audit priorities, not an opaque model-based complexity label.'),('EDGE_CASE','Evidence-heavy denotes evidence presence, not proven ambiguity; score rankings need validation against actual implementation effort.')])
        top=result.sort_values(['score','event_count','request_id'],ascending=[False,False,True]).iloc[0]; user=top.user_id
        ev=self.converted[self.converted.user_id.eq(user)].copy(); ev['timeline_date']=ev.date.fillna(pd.to_datetime(ev.event_date)); currency=r.loc[user,'home_currency']
        fig,ax=plt.subplots(figsize=(13,5))
        for status,g in ev.groupby('status'):
            signed=g.home_amount*np.where(g.direction.eq('debit'),-1,1); ax.scatter(g.timeline_date,signed,label=status,alpha=.7,s=25)
        date=pd.Timestamp(r.loc[user,'request_date']); ax.axvline(date,color='black',linestyle='--',label='Request date'); ax.axhline(0,color='grey',linewidth=.7)
        ax.set(title=f'Complex-user financial-event timeline: {top.request_id} / {user}',xlabel='Settlement date (event date if missing)',ylabel=f'Signed event amount ({currency}); not balance'); ax.legend(); fig.autofmt_xdate(); self.plot('13_complex_timeline',fig)
        self.findings('complex_timeline',{'request_id':top.request_id,'user_id':user,'score':int(top.score),'events':len(ev),'unplottable_amounts':int(ev.home_amount.isna().sum())},[f"{top.request_id} has score {top.score}/7 and {len(ev)} supplied events, selected by score, then event count, then request ID.",f"{ev.home_amount.isna().sum()} amounts cannot be plotted after exact-rate conversion; markers show cash-event records, not a reconstructed balance or recommended plan."], [('DETERMINISTIC','Build an auditable dated ledger before simulating balances; historical event plots must not be mistaken for current available cash.')])

    def conclusions(self):
        sec=self.summary['sections']; stats=lambda k:sec[k]['statistics']; tier=stats('complexity')['tiers']; types=stats('event_types')['types']; top=max(types,key=types.get)
        answers=[
            ('What can be handled completely deterministically?', 'Identifier joins, typed parsing, preference filters, exact FX lookups, cash-state rules, supplied option arithmetic/schedules, output validation and plan simulation once financial facts are normalized.'),
            ('What requires statistical inference?', f"Cadence, variable essential spending, and recurrence amount estimation: {stats('recurrence_intervals')['iqr_above_7_share']:.1%} of candidate series have interval IQR above seven days."),
            ('What needs LLM or vision interpretation?', f"{stats('message_triage')['messages']} evaluation-user messages and image-backed amounts require semantic triage/interpretation. The three reviewed document types have different monetary fields. This EDA does not establish that an LLM outperforms rules/OCR; test that in later ablations."),
            ('What financial-event patterns are most common?', f"{top} is the largest event type ({types[top]:,} rows); settled rows dominate. {stats('recurrence_intervals')['monthly_share']:.1%} of recurrence candidates have a monthly-range median gap."),
            ('What are the major outliers and edge cases?', f"Nonpositive headroom: {stats('amount_headroom')['nonpositive_headroom']} requests; missing exact FX: {stats('fx_coverage')['missing_rate_rows']} foreign cash rows; linked lifecycle records: {stats('lifecycle')['linked_rows']}. Missing image amounts, failed-but-outstanding bills and conflicting dates remain explicit review cases."),
            ('Which recurrence heuristic performed best?', f"Date winner(s): {', '.join(stats('recurrence_backtest')['ties']['date'])}, MAE {self.winners['date']['mean_error']:.3f} days. Amount winner(s): {', '.join(stats('recurrence_backtest')['ties']['amount'])}, normalized error {self.winners['amount']['mean_error']:.4f}. Equal-series one-step backtesting does not establish a universally safe forecast."),
            ('Which variables most strongly affect complexity?', 'The largest declared score components are '+', '.join(f'{k} ({v} requests)' for k,v in list(stats('complexity')['components'].items())[:3])+'. This is definitional contribution, not causal importance or a validated difficulty model.'),
            ('How many requests are simple versus evidence-heavy?', f"Simple: {tier.get('simple',0)}; evidence-heavy: {tier.get('evidence-heavy',0)}; structured-complex: {tier.get('structured-complex',0)}. Tiers are exhaustive and mutually exclusive under the published triage rule."),
            ('Which assumptions are dangerous to hardcode?', 'Every month is 30 days; all salary continues indefinitely; latest amount repeats; linked means duplicate; failed means no remaining liability; pending credit is available; blank equals zero; all users accept full payment; headroom equals safe capacity; every message is available at request time.'),
            ('What should the final architecture contain?', 'Validated ingestion and provenance → evidence interpretation with uncertainty → lifecycle and cash-state reconciliation → statistical recurrence/essential-spending forecasts → deterministic 90-day simulator → eligible plan generation/ranking → independent invariant checks → grounded explanations, final output and usage reporting. Route evidence by explicit flags, and introduce an LLM only after measured rule/OCR failures.')]
        text='# EDA Conclusions for Architecture Design\n\n'+'\n\n'.join(f'### {i}. {q}\n\n{a}' for i,(q,a) in enumerate(answers,1)); display(Markdown(text)); self.report_parts.append(text)
        self.summary['architecture_conclusions']=[{'question':q,'answer':a} for q,a in answers]
        self.summary['artifacts']+=['eda_outputs/tables/recurring_candidates.csv','eda_outputs/tables/recurrence_folds.csv','eda_outputs/tables/recurrence_error_breakdown.csv','eda_outputs/tables/message_topic_flags.csv','eda_outputs/eda_findings.md','plan/eda.ipynb']
        (OUT/'eda_summary.json').write_text(json.dumps(clean(self.summary),indent=2,allow_nan=False)+'\n',encoding='utf-8')
        (OUT/'eda_findings.md').write_text('\n\n'.join(self.report_parts)+'\n',encoding='utf-8')
        assert len(list((OUT/'figures').glob('*.png')))==13
        assert self.complexity_table.request_id.nunique()==len(self.r)
        assert (pd.to_datetime(self.errors.train_end)<pd.to_datetime(self.errors.target_date)).all()
        assert (pd.to_datetime(self.errors.target_date)<pd.to_datetime(self.errors.request_date)).all()
        for name,digest in self.summary['input_sha256'].items():
            assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
        display(Markdown('**Verification:** 13 figures, all evaluation requests assigned a tier, chronological holdouts checked, strict JSON saved, and input fingerprints unchanged.'))

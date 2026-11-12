#!/usr/bin/env python3
"""RGR IoT variant 3: reproducible synthetic weather station, edge clean, SQLite, statistics and compression.
No real-world sensor data or actual cloud operations are implied by local execution.
"""
from __future__ import annotations
import argparse, bz2, csv, gzip, hashlib, json, math, sqlite3, zlib
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

SENSORS = {
    'wind_speed': ('Швидкість вітру', 'м/с', 0, 40),
    'temperature': ('Температура', '°C', -40, 50),
    'precipitation': ('Опади', 'мм/добу', 0, 150),
    'solar_radiation': ('Сонячна радіація', 'Вт/м²', 0, 1200),
}
FIELDS = list(SENSORS)

def generate(seed=2026):
    rng = np.random.default_rng(seed)
    dates = pd.date_range('2025-01-01', '2025-12-31', freq='D')
    day = np.arange(len(dates))
    warm = np.sin(2*np.pi*(day-81)/365)
    temp = 12 + 13*warm + rng.normal(0, 3.1, len(day))
    wind = np.clip(4 + 1.3*np.cos(2*np.pi*day/365) + rng.gamma(1.5, 1.3, len(day)), 0, None)
    p_rain = np.clip(0.42 - 0.11*warm, 0.08, 0.85)
    rain = np.where(rng.random(len(day)) < p_rain, rng.gamma(1.35, 6, len(day)), 0)
    solar = np.maximum(0, 380 + 240*warm + rng.normal(0, 75, len(day)) - rain*3.5)
    df = pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'wind_speed': wind,
                       'temperature': temp, 'precipitation': rain, 'solar_radiation': solar})
    # Insert known, reproducible missing and anomalous values to demonstrate edge cleaning.
    for name in FIELDS:
        missing = rng.choice(len(df), size=8, replace=False)
        remaining = np.setdiff1d(np.arange(len(df)), missing)
        spikes = rng.choice(remaining, size=4, replace=False)
        df.loc[missing, name] = np.nan
        df.loc[spikes, name] = {'wind_speed': 110, 'temperature': 120,
                                 'precipitation': -10, 'solar_radiation': 2500}[name]
    return df

def clean(raw):
    df = raw.copy()
    audit = []
    for name, (label, unit, lower, upper) in SENSORS.items():
        series = pd.to_numeric(df[name], errors='coerce')
        outlier = series.notna() & ~series.between(lower, upper)
        missing = int(series.isna().sum())
        series.loc[outlier] = np.nan
        # Interpolate time-ordered data locally, then fill end points.
        series = series.interpolate(method='linear', limit_direction='both')
        df[name] = series.round(3)
        audit.append({'sensor': name, 'missing_before': missing, 'invalid_before': int(outlier.sum()),
                      'missing_after': int(df[name].isna().sum())})
    return df, audit

def save_sqlite(df, dest):
    con = sqlite3.connect(dest)
    try:
        con.execute('PRAGMA foreign_keys = ON')
        con.executescript('''
        DROP TABLE IF EXISTS measurements;
        DROP TABLE IF EXISTS sensors;
        CREATE TABLE sensors(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, label TEXT NOT NULL, unit TEXT NOT NULL);
        CREATE TABLE measurements(id INTEGER PRIMARY KEY, sensor_id INTEGER NOT NULL, observed_at TEXT NOT NULL,
          value REAL NOT NULL, FOREIGN KEY(sensor_id) REFERENCES sensors(id), UNIQUE(sensor_id, observed_at));
        CREATE INDEX idx_measurements_time ON measurements(observed_at);
        ''')
        for sensor_id, (name, (label, unit, _, _)) in enumerate(SENSORS.items(), 1):
            con.execute('INSERT INTO sensors(id,name,label,unit) VALUES (?,?,?,?)',(sensor_id,name,label,unit))
            entries = [(sensor_id, row.date, float(getattr(row, name))) for row in df.itertuples(index=False)]
            con.executemany('INSERT INTO measurements(sensor_id,observed_at,value) VALUES(?,?,?)', entries)
        con.commit()
        count = con.execute('SELECT COUNT(*) FROM measurements').fetchone()[0]
        assert count == len(df) * len(FIELDS), (count,len(df))
        return count
    finally:
        con.close()

def charts(df, dest):
    d = pd.to_datetime(df['date'])
    fig, axes = plt.subplots(4,1,figsize=(11,9),sharex=True)
    for ax, (name, (label, unit, _, _)) in zip(axes,SENSORS.items()):
        ax.plot(d, df[name], linewidth=1.1)
        ax.set_ylabel(f'{label}\n({unit})',fontsize=9)
        ax.grid(alpha=.25)
    fig.suptitle('Локальна метеостанція: змодельовані щоденні вимірювання за 2025 рік')
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(dest/'timeseries.png',dpi=160); plt.close(fig)
    season = pd.cut(d.dt.month,[0,2,5,8,11,12],labels=['Зима','Весна','Літо','Осінь','Грудень'],include_lowest=True)
    # Group December with January/February.
    season = season.astype(str).replace('Грудень','Зима')
    tmp = df.assign(season=season)
    order=['Зима','Весна','Літо','Осінь']
    fig,axs=plt.subplots(2,2,figsize=(11,7))
    for ax,(name,(label,unit,_,_)) in zip(axs.flat,SENSORS.items()):
        groups=[tmp.loc[tmp.season==s,name].to_numpy() for s in order]
        ax.boxplot(groups, tick_labels=order,showfliers=False)
        ax.set_title(f'{label} ({unit})'); ax.grid(axis='y',alpha=.25)
    fig.suptitle('Сезонний розподіл метеопоказників (модель)');fig.tight_layout();fig.savefig(dest/'seasons.png',dpi=160);plt.close(fig)
    matrix = df[FIELDS].corr()
    fig,ax=plt.subplots(figsize=(8,6));im=ax.imshow(matrix,vmin=-1,vmax=1,cmap='coolwarm')
    ax.set_xticks(range(4),['Вітер','Температура','Опади','Радіація'],rotation=25,ha='right')
    ax.set_yticks(range(4),['Вітер','Температура','Опади','Радіація'])
    for i in range(4):
        for j in range(4): ax.text(j,i,f'{matrix.iloc[i,j]:.2f}',ha='center',va='center')
    fig.colorbar(im,ax=ax);ax.set_title('Кореляції між показниками (синтетичні дані)')
    fig.tight_layout();fig.savefig(dest/'correlation.png',dpi=160);plt.close(fig)
    t=df['temperature'].to_numpy(); x=np.arange(len(t));coefs=np.polyfit(x,t,1)
    fig,ax=plt.subplots(figsize=(10,3.6));ax.plot(d,t,alpha=.65,label='Температура')
    ax.plot(d,np.polyval(coefs,x),label='Лінійний тренд',lw=2)
    ax.set_ylabel('°C');ax.legend();ax.grid(alpha=.3);fig.tight_layout();fig.savefig(dest/'trend.png',dpi=160);plt.close(fig)
    return float(coefs[0])

def main(output='rgr3_assets',seed=2026):
    dest=Path(output);dest.mkdir(parents=True,exist_ok=True)
    raw=generate(seed);clean_df,audit=clean(raw)
    raw.to_csv(dest/'raw.csv',index=False,encoding='utf-8-sig')
    clean_df.to_csv(dest/'clean.csv',index=False,encoding='utf-8-sig')
    with open(dest/'cleaning_audit.csv','w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(audit[0]));w.writeheader();w.writerows(audit)
    count=save_sqlite(clean_df,dest/'weather.sqlite')
    trend=charts(clean_df,dest)
    summary=clean_df[FIELDS].agg(['mean','median','min','max','std']).T
    summary.index.name='sensor'; summary.to_csv(dest/'summary.csv',encoding='utf-8-sig',float_format='%.3f')
    # Keep one stable byte representation for all compression algorithms.
    payload={'metadata': {'variant':3,'year':2025,'seed':seed,'kind':'synthetic','n_days':len(clean_df),
                          'n_measurements':count,'units':{k:v[1] for k,v in SENSORS.items()}},
             'records':clean_df.to_dict(orient='records')}
    raw_bytes=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    (dest/'payload.json').write_bytes(raw_bytes)
    encoded={'original':raw_bytes,'gzip':gzip.compress(raw_bytes,compresslevel=9,mtime=0),
             'zlib':zlib.compress(raw_bytes,level=9),'bzip2':bz2.compress(raw_bytes,compresslevel=9)}
    (dest/'payload.json.gz').write_bytes(encoded['gzip'])
    (dest/'payload.json.zlib').write_bytes(encoded['zlib'])
    (dest/'payload.json.bz2').write_bytes(encoded['bzip2'])
    for fmt,decoded in [('gzip',gzip.decompress(encoded['gzip'])),('zlib',zlib.decompress(encoded['zlib'])),
                        ('bzip2',bz2.decompress(encoded['bzip2']))]: assert decoded==raw_bytes,fmt
    sizes={k:len(v) for k,v in encoded.items()}
    sizes['best']=min(('gzip','zlib','bzip2'),key=lambda k:sizes[k])
    sizes['compression_ratio_pct']={k:round(100*sizes[k]/sizes['original'],2) for k in ('gzip','zlib','bzip2')}
    sizes['sha256']=hashlib.sha256(raw_bytes).hexdigest()
    (dest/'compression.json').write_text(json.dumps(sizes,indent=2,ensure_ascii=False),encoding='utf-8')
    report={'days':len(clean_df),'sensors':len(FIELDS),'sqlite_measurements':count,'cleaning':audit,
            'temperature_linear_trend_c_per_day':round(trend,4),'compression':sizes,
            'local_run':True,'firebase_uploaded':False,'colab_downloaded':False}
    (dest/'run_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('LOCAL RUN OK: days=',len(clean_df),'sensors=',len(FIELDS),'sqlite_measurements=',count)
    print('Missing/outliers cleaned:',audit)
    print('Compression bytes:',sizes)
    print('Files saved:',dest.resolve())
    print('Firebase/Colab are NOT tested until separately configured and run.')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='rgr3_assets');parser.add_argument('--seed',type=int,default=2026)
    args=parser.parse_args();main(args.output,args.seed)

from pyiso.base import BaseClient
import numpy as np
import pandas as pd
from datetime import timedelta


class NYISOClient(BaseClient):
    NAME = 'NYISO'

    base_url = 'http://mis.nyiso.com/public/csv'

    TZ_NAME = 'America/New_York'

    def utcify(self, *args, **kwargs):
        # regular utcify
        ts = super(NYISOClient, self).utcify(*args, **kwargs)

        # timestamp is end of interval
        freq = self.options.get('freq', self.FREQUENCY_CHOICES.fivemin)
        if freq == self.FREQUENCY_CHOICES.fivemin:
            ts -= timedelta(minutes=5)
        else:
            raise ValueError('Not sure whether this freq is allowed for')

        # return
        return ts

    def get_load(self, latest=False, start_at=False, end_at=False, **kwargs):
        # set args
        self.handle_options(data='load', latest=latest,
                            start_at=start_at, end_at=end_at, **kwargs)

        # get data
        if self.options['forecast']:
            df = self.get_any('isolf', self.parse_load)
            extras = {
                'ba_name': self.NAME,
                'freq': self.FREQUENCY_CHOICES.hourly,
                'market': self.MARKET_CHOICES.dam,
            }
        else:
            df = self.get_any('pal', self.parse_load)
            extras = {
                'ba_name': self.NAME,
                'freq': self.FREQUENCY_CHOICES.fivemin,
                'market': self.MARKET_CHOICES.fivemin,
            }

        # serialize and return
        return self.serialize_faster(df, extras=extras)

    def get_trade(self, latest=False, start_at=False, end_at=False, **kwargs):
        # set args
        self.handle_options(data='trade', latest=latest,
                            start_at=start_at, end_at=end_at, **kwargs)

        # get data
        df = self.get_any('ExternalLimitsFlows', self.parse_trade)
        extras = {
            'ba_name': self.NAME,
            'freq': self.FREQUENCY_CHOICES.fivemin,
            'market': self.MARKET_CHOICES.fivemin,
        }

        # serialize and return
        return self.serialize_faster(df, extras=extras)

    def get_any(self, label, parser):
        # set up storage
        pieces = []

        # fetch and parse all csvs
        for date in self.dates():
            content = self.fetch_csv(date, label)
            pieces.append(parser(content))

        # combine and slice
        df = pd.concat(pieces)
        sliced = self.slice_times(df)

        # return
        return sliced

    def fetch_csv(self, date, label):
        # construct url
        datestr = date.strftime('%Y%m%d')
        url = '%s/%s/%s%s.csv' % (self.base_url, label, datestr, label)

        # make request
        result = self.request(url)

        # return content
        return result.text

    def parse_load(self, content):
        # parse csv to df
        df = self.parse_to_df(content)

        # total load grouped by timestamp
        try:
            total_loads = df.groupby('Time Stamp').aggregate(np.sum)
        except KeyError:
            raise ValueError('Could not parse content:\n%s' % content)

        # set index
        total_loads['timestamp'] = total_loads.index.map(pd.to_datetime)
        total_loads.set_index('timestamp', inplace=True)
        total_loads.index = self.utcify_index(total_loads.index)

        # pull out column
        series = total_loads['Load']
        final_df = pd.DataFrame({'load_MW': series})

        # return
        return final_df

    def parse_trade(self, content):
        # parse csv to df
        df = self.parse_to_df(content)
        try:
            df.drop_duplicates(['Timestamp', 'Interface Name'], inplace=True)
        except KeyError:
            raise ValueError('Could not parse content:\n%s' % content)

        # pivot
        pivoted = df.pivot(index='Timestamp', columns='Interface Name', values='Flow (MWH)')

        # only keep flows across external interfaces
        interfaces = [
            'SCH - HQ - NY', 'SCH - HQ_CEDARS', 'SCH - HQ_IMPORT_EXPORT',  # HQ
            'SCH - NE - NY', 'SCH - NPX_1385', 'SCH - NPX_CSC',  # ISONE
            'SCH - OH - NY',  # Ontario
            'SCH - PJ - NY', 'SCH - PJM_HTP', 'SCH - PJM_NEPTUNE', 'SCH - PJM_VFT',  # PJM
        ]
        subsetted = pivoted[interfaces].copy()

        # set index
        subsetted['timestamp'] = subsetted.index.map(pd.to_datetime)
        subsetted.set_index('timestamp', inplace=True)
        subsetted.index = self.utcify_index(subsetted.index)

        # sum up
        cleaned = subsetted.dropna(axis=0)
        series = cleaned.apply(lambda x: -1*np.sum(x), axis=1)
        final_df = pd.DataFrame({'net_exp_MW': series})

        # return
        return final_df

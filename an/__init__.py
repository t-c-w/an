"""
Scraping and parsing amazon.
"""

import ut as ms
import pandas as pd
import numpy as np
import requests
import re
from bs4 import BeautifulSoup
from datetime import timedelta
from datetime import datetime
import matplotlib as mpl

mpl.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as dates
import tempfile
from ut.viz.util import insert_nans_in_x_and_y_when_there_is_a_gap_in_x
import pylab


class Amazon(object):
    url_template = dict()
    url_template['product_page'] = 'https://www.amazon.{country}/dp/{asin}/'
    url_template['product_reviews'] = (
        'https://www.amazon.{country}/product-reviews/{asin}/'
    )

    regexp = dict()
    regexp['nreviews_re'] = {
        'com': re.compile(r'\d[\d,]*(?= customer review)'),
        'co.uk': re.compile(r'\d[\d,]*(?= customer review)'),
        'in': re.compile(r'\d[\d,]*(?= customer review)'),
        'de': re.compile(r'\d[\d\.]*(?= Kundenrezens\w\w)'),
    }
    regexp['no_reviews_re'] = {
        'com': re.compile(r'no customer reviews'),
        'co.uk': re.compile(r'no customer reviews'),
        'in': re.compile(r'no customer reviews'),
        'de': re.compile(r'Noch keine Kundenrezensionen'),
    }
    # regexp['average_rating_re'] = {'com': re.compile('')}
    default = dict()
    default['country'] = 'com'
    # default['requests_kwargs'] = {}
    default['requests_kwargs'] = {
        'proxies': {'http': 'http://us.proxymesh.com:31280'},
        'auth': requests.auth.HTTPProxyAuth(
            ms.util.importing.get_environment_variable('PROXYMESH_USER'),
            ms.util.importing.get_environment_variable('PROXYMESH_PASS'),
        ),
    }

    @classmethod
    def url(cls, what='product_page', **kwargs):
        kwargs = dict(Amazon.default, **kwargs)
        return cls.url_template[what].format(**kwargs)

    @classmethod
    def slurp(cls, what='product_page', **kwargs):
        kwargs = dict(Amazon.default, **kwargs)
        r = requests.get(
            Amazon.url(what=what, **kwargs), **Amazon.default['requests_kwargs']
        )
        if r.status_code == 200:
            return r.text
        else:  # try again and return no matter what
            r = requests.get(
                Amazon.url(what=what, **kwargs), **Amazon.default['requests_kwargs']
            )
            return r.text

    @classmethod
    def get_info(cls, asin, country='co.uk', **kwargs):
        info = {'date': datetime.now()}
        info = dict(
            info, **{'sales_ranks': cls.get_sales_rank(asin, country='co.uk', **kwargs)}
        )
        # info = dict(info, **{'num_of_reviews': cls.get_number_of_reviews(asin, country='co.uk', **kwargs)})
        return info

    @classmethod
    def get_sales_rank(cls, **kwargs):
        html = Amazon.slurp(what='product_page', **kwargs)
        sales_rank = [Amazon.parse_sales_rank(html, **kwargs)]
        sales_rank += Amazon.parse_sales_sub_rank(html, **kwargs)
        # Filter out any None values that might result from parsing failures
        sales_rank = [item for item in sales_rank if item is not None]
        return sales_rank

    @classmethod
    def parse_product_title(cls, b, **kwargs):
        if not isinstance(b, BeautifulSoup):
            b = BeautifulSoup(b, features="lxml")
        t = b.find('span', attrs={'id': 'productTitle'})
        return t.text.strip() if t else None

    @classmethod
    def parse_sales_rank(cls, b, **kwargs):
        if not isinstance(b, BeautifulSoup):
            b = BeautifulSoup(b, features="lxml")

        # Find the main SalesRank li element
        sales_rank_li = b.find('li', attrs={'id': 'SalesRank'})
        if sales_rank_li is None:
            return None

        # Find the main rank information
        main_rank_span = sales_rank_li.find('span', class_='zg_bsr_rank')
        if main_rank_span:
            rank_str = main_rank_span.text.replace('#', '').replace(',', '').strip()
            sales_rank_val = int(rank_str)

            # Find the category
            # The category is usually in a sibling span with class zg_bsr_text, followed by an anchor tag
            category_a = sales_rank_li.find(
                'span', class_='zg_bsr_text'
            ).find_next_sibling('a', class_='a-link-normal')
            sales_rank_category = category_a.text.strip() if category_a else None

            return {
                'sales_rank': sales_rank_val,
                'sales_rank_category': sales_rank_category,
            }
        return None

    @classmethod
    def parse_sales_sub_rank(cls, b, **kwargs):
        if not isinstance(b, BeautifulSoup):
            b = BeautifulSoup(b, features="lxml")

        sales_rank_li = b.find('li', attrs={'id': 'SalesRank'})
        if sales_rank_li is None:
            return []

        sales_sub_rank_list = []
        # Find all li elements within the a-unordered-list that represents sub-ranks
        # Some sub-ranks are directly under li, others are wrapped in a-list-item span
        # We need to consider both cases.
        sub_rank_elements = sales_rank_li.select(
            'ul.a-unordered-list.a-nostyle.a-vertical > li'
        )

        for item_li in sub_rank_elements:
            d = {}
            # Check for the primary sub-rank structure first
            rank_span = item_li.find('span', class_='zg_bsr_rank')
            if rank_span:
                try:
                    d['sales_rank'] = int(
                        rank_span.text.replace('#', '').replace(',', '').strip()
                    )
                except ValueError:
                    continue  # Skip if rank cannot be parsed

                category_elements = item_li.find_all(
                    ['span', 'a'], class_=['zg_bsr_text', 'a-link-normal']
                )
                categories = []
                for elem in category_elements:
                    if elem.name == 'span' and 'zg_bsr_text' in elem.get('class', []):
                        # This span contains "in" and potentially part of the category. Ignore "in".
                        pass
                    elif elem.name == 'a' and 'a-link-normal' in elem.get('class', []):
                        categories.append(elem.text.strip())
                    elif elem.name == 'span' and 'a-list-item' not in elem.get(
                        'class', []
                    ):
                        # Catch other relevant spans that might be part of the category path.
                        text = elem.text.strip()
                        if (
                            text and text.lower() != 'in'
                        ):  # Avoid adding "in" or empty strings
                            categories.append(text)

                # If no specific categories were found, but the item itself is an a-list-item span,
                # we might need to get categories from within that span.
                if not categories:
                    # This handles cases like: <span class="a-list-item"><span class="zg_bsr_rank">#1</span><span class="zg_bsr_text">in </span><a ...>Category</a></span>
                    list_item_span = item_li.find('span', class_='a-list-item')
                    if list_item_span:
                        inner_category_elements = list_item_span.find_all(
                            ['span', 'a'], class_=['zg_bsr_text', 'a-link-normal']
                        )
                        for elem in inner_category_elements:
                            if elem.name == 'a' and 'a-link-normal' in elem.get(
                                'class', []
                            ):
                                categories.append(elem.text.strip())
                            elif elem.name == 'span' and 'a-list-item' not in elem.get(
                                'class', []
                            ):
                                text = elem.text.strip()
                                if text and text.lower() != 'in':
                                    categories.append(text)

                d['sales_rank_category'] = categories
                if (
                    d.get('sales_rank') is not None and categories
                ):  # Only add if both rank and category are found
                    sales_sub_rank_list.append(d)
        return sales_sub_rank_list

    @classmethod
    def parse_avg_rating(cls, b, **kwargs):
        if not isinstance(b, BeautifulSoup):
            b = BeautifulSoup(b, features="lxml")
        t = b.find('span', 'reviewCountTextLinkedHistogram')
        return (
            float(re.compile(r'[\d\.]+').findall(t['title'])[0])
            if t and 'title' in t.attrs
            else None
        )

    @staticmethod
    def test_rating_scrape_with_vanessas_book():
        html = Amazon.slurp(what='product_page', country='co.uk', asin='1857886127')

    @staticmethod
    def get_number_of_reviews(asin, country, **kwargs):
        url = 'http://www.amazon.{country}/product-reviews/{asin}'.format(
            country=country, asin=asin
        )
        html = requests.get(url).text
        try:
            return int(
                re.compile(r'\D').sub(
                    '', Amazon.regexp['nreviews_re'][country].search(html).group(0)
                )
            )
        except Exception:
            if Amazon.regexp['no_reviews_re'][country].search(html):
                return 0
            else:
                return None  # to distinguish from 0, and handle more cases if necessary


class AmazonBookWatch(object):
    from pymongo import MongoClient

    default = dict()
    default['product_list'] = [
        {'title': 'The Nanologues', 'asin': '9350095173'},
        {'title': 'Never mind the bullocks', 'asin': '1857886127'},
        {'title': 'The Other Side of Paradise', 'asin': '1580055311'},
    ]
    default['watch_list'] = [
        {'title': 'The Nanologues', 'asin': '9350095173', 'country': 'in'},
        {'title': 'The Nanologues', 'asin': '9350095173', 'country': 'co.uk'},
        {'title': 'The Nanologues', 'asin': '9350095173', 'country': 'com'},
        {'title': 'Never mind the bullocks', 'asin': '1857886127', 'country': 'in'},
        {'title': 'Never mind the bullocks', 'asin': '1857886127', 'country': 'co.uk'},
        {'title': 'Never mind the bullocks', 'asin': '1857886127', 'country': 'com'},
        {'title': 'The Other Side of Paradise', 'asin': '1580055311', 'country': 'com'},
        {
            'title': 'The Other Side of Paradise',
            'asin': '1580055311',
            'country': 'co.uk',
        },
        {
            'title': "Heaven's Harlots (Paperback)",
            'asin': '0688170129',
            'country': 'com',
        },
        {
            'title': "Heaven's Harlots (Hardcover)",
            'asin': '0688155049',
            'country': 'com',
        },
        {'title': 'Women on Ice', 'asin': '0813554594', 'country': 'com'},
    ]
    default['frequency_in_hours'] = 1
    default['max_date_ticks'] = 200
    default['stats_num_of_days'] = 1
    default['figheight'] = 3
    default['figwidth'] = 14
    default['linewidth'] = 3
    default['tick_font_size'] = 13
    default['label_fontsize'] = 13
    default['title_fontsize'] = 15
    default['line_style'] = '-bo'
    default['facecolor'] = 'blue'
    default['save_format'] = 'png'
    default['dpi'] = 40
    default[
        'book_info_html_template'
    ] = '''<hr>
        <h3>{book_title} - {country} - {num_of_reviews} reviews </h3>
    '''
    default['category_html'] = (
        '<img style="box-shadow:         3px 3px 5px 6px #ccc;" src={image_url}>'
    )
    db = MongoClient()['misc']['book_watch']

    def __init__(self, **kwargs):
        from ut.serialize.s3 import S3

        self.s3 = S3(bucket_name='public-ut-images', access_key='ut')
        attribute_name = 'product_list'
        setattr(
            self,
            attribute_name,
            kwargs.get(attribute_name, None) or AmazonBookWatch.default[attribute_name],
        )
        attribute_name = 'watch_list'
        setattr(
            self,
            attribute_name,
            kwargs.get(attribute_name, None) or AmazonBookWatch.default[attribute_name],
        )

    def asin_of_title(self, title):
        the_map = {
            k: v
            for k, v in zip(
                [x['title'] for x in self.product_list],
                [x['asin'] for x in self.product_list],
            )
        }
        return the_map[title]

    def get_book_statuses(self):
        now = datetime.now()
        info_list = list()
        for book in self.watch_list:
            try:
                info = dict({'date': now}, **book)
                info = dict(info, **{'sale_ranks': Amazon.get_sales_rank(**book)})
                info = dict(
                    info, **{'num_of_reviews': Amazon.get_number_of_reviews(**book)}
                )
                info_list.append(info)
            except Exception:
                continue
        return info_list

    @staticmethod
    def cursor_to_df(cursor):
        d = ms.dacc.mong.util.to_df(cursor, 'sale_ranks')
        d = process_sales_rank_category(d)
        return d

    @staticmethod
    def get_min_max_sales_rank_dates(book_info):
        cumul = list()
        for x in list(book_info['sales_rank'].values()):
            try:
                cumul += x['data']['date'].tolist()
            except Exception:
                raise
        return [np.min(cumul), np.max(cumul)]

    def mk_book_info(self, title, country, **kwargs):
        book_info = dict()
        kwargs = dict(kwargs, **self.default)
        d = AmazonBookWatch.cursor_to_df(
            self.db.find(spec={'title': title, 'country': country})
            .sort([('_id', -1)])
            .limit(kwargs['max_date_ticks'])
        )
        book_info['num_reviews'] = np.max(d['num_of_reviews'])
        book_info['sales_rank'] = dict()
        d = d[['date', 'sales_rank_category', 'sales_rank_subcategory', 'sales_rank']]
        categories = np.unique(d['sales_rank_category'])
        for c in categories:
            dd = d[d['sales_rank_category'] == c].sort_values('date', ascending=True)
            book_info['sales_rank'][c] = dict()
            book_info['sales_rank'][c]['sales_rank_subcategory'] = dd[
                'sales_rank_subcategory'
            ].iloc[0]
            dd = dd[['date', 'sales_rank']]
            book_info['sales_rank'][c]['data'] = dd
            ddd = dd[
                dd['date']
                > datetime.now() - timedelta(days=kwargs['stats_num_of_days'])
            ]
            book_info['sales_rank'][c]['rank_stats'] = pd.DataFrame(
                [
                    {
                        'hi_rank': np.min(ddd['sales_rank']),
                        'mean_rank': np.round(np.mean(ddd['sales_rank'])),
                        'lo_rank': np.max(ddd['sales_rank']),
                    }
                ]
            )
            book_info['sales_rank'][c]['rank_stats'] = book_info['sales_rank'][c][
                'rank_stats'
            ][['hi_rank', 'mean_rank', 'lo_rank']]
        book_info['commun_date_range'] = self.get_min_max_sales_rank_dates(book_info)
        return book_info

    def mk_sales_rank_plot(self, d, category='', save_filename=True, **kwargs):
        kwargs = dict(kwargs, **self.default)
        if isinstance(d, dict):
            if 'sales_rank' in list(d.keys()):
                d = d['sales_rank'][category]['data']
            elif category in list(d.keys()):
                d = d[category]['data']
            elif 'data' in list(d.keys()):
                d = d['data']
            else:
                raise ValueError(
                    'Your dict must have a "data" key or a %s key' % category
                )
        d = d.sort_values('date')
        x = [xx.to_datetime() if hasattr(xx, 'to_datetime') else xx for xx in d['date']]
        y = list(d['sales_rank'])

        gap_thresh = timedelta(seconds=kwargs['frequency_in_hours'] * 4.1 * 3600)
        x, y = insert_nans_in_x_and_y_when_there_is_a_gap_in_x(
            x, y, gap_thresh=gap_thresh
        )
        fig, ax = plt.subplots(1)

        fig.set_figheight(kwargs['figheight'])
        fig.set_figwidth(kwargs['figwidth'])
        ax.plot(x, y, kwargs['line_style'], linewidth=kwargs['linewidth'])
        commun_date_range = kwargs.get('commun_date_range', None)
        if commun_date_range:
            pylab.xlim(kwargs['commun_date_range'])
        ax.fill_between(x, y, max(y), facecolor=kwargs['facecolor'], alpha=0.5)

        # plt.ylabel('Amazon (%s) Sales Rank' % category, fontsize=kwargs['label_fontsize'])
        plot_title = kwargs.get('plot_title', 'Amazon (%s) Sales Rank' % category)
        plt.title(plot_title, fontsize=kwargs['title_fontsize'])

        plt.tick_params(axis='y', which='major', labelsize=kwargs['tick_font_size'])
        # plt.tick_params(axis='x', which='major', labelsize=kwargs['tick_font_size'])
        plt.tick_params(axis='x', which='minor', labelsize=kwargs['tick_font_size'])

        plt.gca().invert_yaxis()
        # ax.xaxis.set_minor_locator(dates.WeekdayLocator(byweekday=(1), interval=1))
        ax.xaxis.set_minor_locator(dates.DayLocator(interval=1))
        ax.xaxis.set_minor_formatter(dates.DateFormatter('%a\n%d %b'))
        ax.xaxis.grid(True, which='minor')
        ax.yaxis.grid()
        ax.xaxis.set_major_locator(dates.MonthLocator())
        # ax.xaxis.set_major_formatter(dates.DateFormatter('\n\n\n%b\n%Y'))
        plt.tight_layout()
        if save_filename:
            if isinstance(save_filename, str):
                save_filename = save_filename + '.' + kwargs['save_format']
            else:  # save to temp file
                save_filename = tempfile.NamedTemporaryFile().name
            plt.savefig(save_filename, format=kwargs['save_format'], dpi=kwargs['dpi'])
            return save_filename
        else:
            return None

    def mk_book_info_html(self, title, country, **kwargs):
        kwargs = dict(kwargs, **self.default)
        book_info = self.mk_book_info(title, country, **kwargs)

        html = kwargs['book_info_html_template'].format(
            book_title=title, country=country, num_of_reviews=book_info['num_reviews']
        )
        html = html + '<br>\n'
        for category in list(book_info['sales_rank'].keys()):
            # make and save a graph, send to s3, and return a url for it
            file_name = self.mk_sales_rank_plot(
                d=book_info['sales_rank'],
                category=category,
                save_filename=True,
                commun_date_range=book_info['commun_date_range'],
                plot_title='Amazon.%s (%s) Sales Rank'
                % (
                    country,
                    book_info['sales_rank'][category]['sales_rank_subcategory'],
                ),
                **kwargs
            )
            s3_key_name = '{title} - {country} - {category} - {date}.png'.format(
                title=title,
                country=country,
                category=category,
                date=datetime.now().strftime('%Y%m%d'),
            )
            self.s3.dumpf(file_name, s3_key_name)
            image_url = self.s3.get_http_for_key(s3_key_name)
            html = html + kwargs['category_html'].format(image_url=image_url) + '<br>\n'
        # html = html + "\n<br>"
        return html

    def mk_html_report(self, title_country_list=None):
        title_country_list = title_country_list or [
            {'title': 'Never mind the bullocks', 'country': 'co.uk'},
            {'title': 'Never mind the bullocks', 'country': 'com'},
            {'title': 'The Nanologues', 'country': 'in'},
        ]
        html = ''

        html += 'Stats of the last 24 hours:<br>'
        d = pd.DataFrame()
        for title_country in title_country_list:
            title = title_country['title']
            country = title_country['country']
            book_info = self.mk_book_info(title=title, country=country)
            for category in list(book_info['sales_rank'].keys()):
                dd = pd.concat(
                    [
                        pd.DataFrame(
                            [{'title': title, 'country': country, 'category': category}]
                        ),
                        book_info['sales_rank'][category]['rank_stats'],
                    ],
                    axis=1,
                )
                d = pd.concat([d, dd])
        d = d[['title', 'country', 'category', 'lo_rank', 'mean_rank', 'hi_rank']]

        html += ms.daf.to.to_html(
            d,
            template='box-table-c',
            index=False,
            float_format=lambda x: '{:,.0f}'.format(x),
        )

        for title_country in title_country_list:
            title = title_country['title']
            country = title_country['country']
            html += self.mk_book_info_html(title=title, country=country)

        return html


def process_sales_rank_category(d):
    d['sales_rank_subcategory'] = [
        ' > '.join(x) if isinstance(x, list) else x for x in d['sales_rank_category']
    ]
    d['sales_rank_category'] = [
        x[-1] if isinstance(x, list) else x for x in d['sales_rank_category']
    ]
    return d


def test_with_test_html():  # works on 2025-06-13
    asin = 'B09VZH6NS1'
    country = 'fr'  # Change to desired Amazon region
    # For testing purposes, using the provided HTML content directly
    html_content_for_test = """
<!doctype html><html lang="en-us" class="a-no-js" data-19ax5a9jf="dingo">
<head><script>var aPageStart = (new Date()).getTime();</script><meta charset="utf-8"/>
</head>
<body>
<li id="SalesRank">
    <span>
        <span>Amazon Best Sellers Rank:</span>
        <ul class="a-unordered-list a-nostyle a-vertical">
            <li>
                <span class="zg_bsr_rank">#1,418</span>
                <span class="zg_bsr_text">in </span>
                <a class="a-link-normal" href="/gp/bestsellers/automotive/ref=zg_bsr_unrec_automotive_1">Automotive</a>
            </li>
            <li>
                <span class="a-list-item">
                    <span class="zg_bsr_rank">#1</span>
                    <span class="zg_bsr_text">in </span>
                    <a class="a-link-normal" href="/gp/bestsellers/automotive/15705351/ref=zg_bsr_unrec_automotive_2">Automotive Replacement Springs</a>
                </span>
            </li>
            <li>
                <span class="a-list-item">
                    <span class="zg_bsr_rank">#5</span>
                    <span class="zg_bsr_text">in </span>
                    <a class="a-link-normal" href="/gp/bestsellers/industrial/ref=zg_bsr_unrec_industrial_1">Industrial & Scientific</a>
                </span>
            </li>
             <li>
                <span class="a-list-item">
                    <span class="zg_bsr_rank">#2</span>
                    <span class="zg_bsr_text">in </span>
                    <a class="a-link-normal" href="/gp/bestsellers/industrial/55325011/ref=zg_bsr_unrec_industrial_2">Industrial Hardware</a>
                </span>
            </li>
            <li>
                <span class="a-list-item">
                    <span class="zg_bsr_rank">#3</span>
                    <span class="zg_bsr_text">in </span>
                    <a class="a-link-normal" href="/gp/bestsellers/industrial/15705351/ref=zg_bsr_unrec_industrial_2">Another Nested Category</a>
                </span>
            </li>
        </ul>
    </span>
</li>
</body></html>
    """
    # Create a BeautifulSoup object from the provided HTML content
    soup = BeautifulSoup(html_content_for_test, 'lxml')

    # Now, call the parsing methods with the BeautifulSoup object
    main_sales_rank = Amazon.parse_sales_rank(soup)
    sub_sales_ranks = Amazon.parse_sales_sub_rank(soup)

    sales_rank = [main_sales_rank] + sub_sales_ranks
    sales_rank = [
        item for item in sales_rank if item is not None
    ]  # Ensure no None values
    print(sales_rank)


if __name__ == '__main__':

    # does NOT work on 2025-06-13 (gives me empty list)
    
    asin = 'B09VZH6NS1'

    country = 'fr'  # Change to desired Amazon region

    sales_rank = Amazon.get_sales_rank(asin=asin, country=country)

    print(sales_rank)

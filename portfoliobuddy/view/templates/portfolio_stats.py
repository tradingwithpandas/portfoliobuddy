
ASSET_CONCENTRATION_TEMPLATE = '''
Asset Concentration{% if liquid_only %} Liquid{% endif %}:

{{ ticker_concentrations }}
'''.strip()

CAN_SELL_TEMPLATE = '''
{% for can_sell_, days_to_sell, tickers in can_sell_summary %}
{{ ', '.join(tickers) }} {% if can_sell_ %} can be sold {% else %} can be sold after {{ days_to_sell }} days {% endif %}
{% endfor %}
'''.strip()

RETURNS_TEMPLATE = '''
Portfolio Returns {% if liquid_only %}Liquid{% endif %}

{{ returns_tbl }}
'''.strip()

VAL_TEMPLATE = '''
Portfolio Value {% if liquid_only %}Liquid{% endif %} {% if not idea_mode %} {{ close_value }}
{% else %}
{{ val_tbl }}
{% endif %}
'''

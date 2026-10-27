#!/usr/bin/env python3
"""Read one date's exact publication container without changing its receipt or credentials."""
import argparse
import json
import os
from pathlib import Path
import sys
from datetime import date
import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
from utils import execution_lock, get_output_root, get_state_root, load_project_dotenv
from instagram_poster import read_publication_record


def report(run_date):
    receipt=read_publication_record(get_output_root()/run_date)
    result={'date':run_date,**{k:receipt[k] for k in ('publication_state','creation_id','post_id',
        'reconciliation_deadline','reconciliation_next_poll_at') if k in receipt}}
    container=receipt.get('creation_id')
    if not container:
        return result | {'container_status':'identity_unavailable'}
    if not isinstance(container,str) or not container.isdigit():
        raise ValueError('Invalid exact container identity')
    state=get_state_root()/'instagram_token_state.json'
    stored=json.loads(state.read_text()) if state.exists() else {}
    token=os.getenv('INSTAGRAM_ACCESS_TOKEN') or stored.get('access_token')
    if not token:
        return result | {'container_status':'authentication_unavailable'}
    from utils import instagram_graph_api_version
    version=instagram_graph_api_version()
    if not __import__('re').fullmatch(r'v\d+\.\d+',version):
        raise ValueError('Invalid Graph API version')
    try:
        response=requests.get(f'https://graph.facebook.com/{version}/{container}',
            params={'fields':'status_code','access_token':token},timeout=30)
        status=response.json().get('status_code') if response.status_code==200 else None
        result.update(http_status=response.status_code,container_status=status if status in
            {'PUBLISHED','ERROR','EXPIRED','FINISHED','IN_PROGRESS'} else 'unresolved')
    except (requests.RequestException,ValueError,TypeError):
        result['container_status']='unreadable'
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('date',type=date.fromisoformat)
    args=parser.parse_args();load_project_dotenv()
    with execution_lock():
        print(json.dumps(report(args.date.isoformat()),sort_keys=True))


if __name__=='__main__':
    main()

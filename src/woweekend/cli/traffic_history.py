"""Prepare descriptive history explicitly; normal pre-race rendering is cache-only."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--circuit',required=True,help='Exact historical calendar Location, e.g. Monza')
    parser.add_argument('--target-season',type=int,required=True)
    parser.add_argument('--data-root')
    parser.add_argument('--seed-history',type=Path,help='Optional validated completed circuit-summary input directory')
    parser.add_argument('--refresh',action='store_true',help='Reanalyse cached inputs / retry missing years explicitly')
    parser.add_argument('--review-dir',type=Path,help='Render only the historical pre-race section and its existing report bundle')
    args=parser.parse_args()
    from wostrategy.analysis.circuit_traffic_history import prepare_history
    from wostrategy.analysis.circuit_traffic_source import HistoricalTrafficSource
    result,path,reused=prepare_history(circuit=args.circuit,target_season=args.target_season,data_root=args.data_root,
        source_loader=HistoricalTrafficSource(circuit=args.circuit,data_root=args.data_root,seed_history=args.seed_history),refresh=args.refresh)
    print(json.dumps(dict(status=result['status'],artifact=str(path),reused=reused,
                         available_years=result['available_years'],unavailable_years=result['unavailable_years']),indent=2))
    if args.review_dir:
        from woweekend.reports.historical_traffic import historical_traffic_section
        from woweekend.reports.bundle import create_report_bundle
        context,figures=historical_traffic_section(circuit=args.circuit,target_season=args.target_season,
            data_root=args.data_root,run_path=args.review_dir)
        create_report_bundle(run_path=args.review_dir,workflow='race_preparation',event=f'{args.target_season}-{args.circuit}',
            context={'historical_traffic':context,'section_only_review':True},results={'historical_traffic':context},
            figures=figures,language='en-GB')


if __name__=='__main__':main()

import datetime as dt
import unittest

from ai_manager.availability import next_availability, countdown


NOW = dt.datetime(2026, 10, 15, 10, tzinfo=dt.timezone.utc)


def account(provider='codex', number='2', **extra):
    return dict(provider=provider, account=number, email=f'user{number}@example.invalid', **extra)


def entry(a, windows):
    return dict(provider=a['provider'], account=a['account'], email=a['email'],
                email_verified=True, status='OK', queried_at=(NOW-dt.timedelta(minutes=1)).isoformat(),
                windows=[dict(name=name, available_percent=value,
                              **({'reset_at': (NOW+dt.timedelta(seconds=seconds)).isoformat()}
                                 if seconds is not None else {})) for name, value, seconds in windows])


class AvailabilityTests(unittest.TestCase):
    def test_latest_exhausted_window_per_account_then_earliest_account(self):
        for provider in ('codex', 'claude'):
            a, b = account(provider, '2'), account(provider, '4')
            rows = [entry(a, [('5h', 0, 100), ('weekly', 0, 7200)]),
                    entry(b, [('5h', 0, 3600), ('weekly', 50, 10000)])]
            result = next_availability([a, b], rows, provider, NOW)
            self.assertEqual(result['status'], 'waiting')
            self.assertEqual(result['account'], '4')
            self.assertEqual(result['reset_at'], NOW+dt.timedelta(hours=1))

    def test_positive_windows_do_not_delay_recovery(self):
        a = account()
        result = next_availability([a], [entry(a, [('5h', 0, 120), ('weekly', 90, 999999)])], 'codex', NOW)
        self.assertEqual(result['reset_at'], NOW+dt.timedelta(seconds=120))

    def test_available_status_and_manual_disabled_other_providers_are_excluded(self):
        a, b = account(), account(number='4')
        rows = [entry(a, [('weekly', 0, 500)]), entry(b, [('weekly', 20, 700)])]
        self.assertEqual(next_availability([a,b], rows, 'codex', NOW)['status'], 'available')
        for setting in ({'automatic':False}, {'enabled':False}):
            with self.subTest(setting=setting):
                changed = dict(b, **setting)
                result = next_availability([a,changed,account('claude')], rows+[entry(account('claude'), [('weekly',100,50)])], 'codex', NOW)
                self.assertEqual(result['status'], 'waiting')
                self.assertEqual(result['account'], '2')

    def test_available_account_also_reports_the_next_exhausted_account_to_return(self):
        for provider in ('codex','claude'):
            with self.subTest(provider=provider):
                a,b,c=account(provider,'2'),account(provider,'4'),account(provider,'6')
                rows=[entry(a,[('weekly',80,100)]),entry(b,[('5h',0,50),('weekly',0,7200)]),
                      entry(c,[('weekly',0,3600)])]
                result=next_availability([a,b,c],rows,provider,NOW)
                self.assertEqual(result['status'],'available')
                self.assertEqual(result['accounts'],['2'])
                self.assertEqual(result['next']['account'],'6')
                self.assertEqual(result['next']['reset_at'],NOW+dt.timedelta(hours=1))

    def test_when_all_accounts_have_quota_the_next_published_reset_is_distinct(self):
        a,b=account(),account(number='4')
        rows=[entry(a,[('5h',50,7200),('weekly',80,90000)]),entry(b,[('5h',40,3600)])]
        result=next_availability([a,b],rows,'codex',NOW)
        self.assertEqual(result['status'],'available')
        self.assertNotIn('next',result)
        self.assertEqual(result['next_reset']['account'],'4')
        self.assertEqual(result['next_reset']['reset_at'],NOW+dt.timedelta(hours=1))

    def test_pending_recheck_is_visible_alongside_an_available_account(self):
        a,b=account(),account(number='4')
        result=next_availability([a,b],[entry(a,[('weekly',80,50)]),entry(b,[('weekly',0,-30)])],'codex',NOW)
        self.assertEqual(result['status'],'available')
        self.assertEqual(result['next']['status'],'due')
        self.assertEqual(result['next']['account'],'4')

    def test_agy_available_family_keeps_the_other_family_recovery_counter(self):
        a=account('agy','current',single=True)
        row=entry(a,[('Gemini Models · 5h',50,100),('Gemini Models · Semanal',70,900),
                     ('Claude and GPT models · 5h',0,400),('Claude and GPT models · Semanal',20,900)])
        result=next_availability([a],[row],'agy',NOW)
        self.assertEqual(result['status'],'available')
        self.assertEqual(result['next']['reset_at'],NOW+dt.timedelta(seconds=400))
        self.assertEqual(result['next']['family'],'Claude and GPT models')

    def test_available_without_published_resets_does_not_invent_a_counter(self):
        a,b=account(),account(number='4',automatic=False)
        result=next_availability([a,b],[entry(a,[('weekly',80,None)]),entry(b,[('weekly',0,20)])],'codex',NOW)
        self.assertEqual(result['status'],'available')
        self.assertNotIn('next',result);self.assertNotIn('next_reset',result)

    def test_missing_invalid_or_unverified_data_never_predicts_availability(self):
        a = account()
        for changes in ({'status':'UNKNOWN'}, {'status':'SIN LOGIN'}, {'email_verified':False},
                        {'email':'other@example.invalid'}, {'queried_at':'bad'}, {'provider':'claude'},
                        {'account':'99'}, {'queried_at':(NOW+dt.timedelta(seconds=5)).isoformat()}):
            with self.subTest(changes=changes):
                row = entry(a, [('weekly',0,50)]);row.update(changes)
                self.assertEqual(next_availability([a],[row],'codex',NOW)['status'],'unknown')
        for value in (None, True, -1, 101, float('nan')):
            with self.subTest(value=value):
                row=entry(a,[('5h',value,50),('weekly',0,100)])
                self.assertEqual(next_availability([a],[row],'codex',NOW)['status'],'unknown')
        for reset in (None, 'tomorrow', '2026-10-16T12:00:00', (NOW-dt.timedelta(days=1)).isoformat()):
            with self.subTest(reset=reset):
                row=entry(a,[('5h',0,50),('weekly',0,100)])
                row['windows'][0]['reset_at']=reset
                self.assertEqual(next_availability([a],[row],'codex',NOW)['status'],'unknown')

    def test_reached_reset_requires_query_instead_of_claiming_available(self):
        a=account();row=entry(a,[('weekly',0,-30)])
        self.assertEqual(next_availability([a],[row],'codex',NOW)['status'],'due')

    def test_historical_success_and_reset_credits_never_unlock_a_failed_query(self):
        a=account();row=entry(a,[('weekly',0,500)])
        row.update(status='UNKNOWN',windows=[],previous_success={'windows':[{'available_percent':100}]},
                   reset_credits_available=10,credits={'balance':9999})
        self.assertEqual(next_availability([a],[row],'codex',NOW)['status'],'unknown')

    def test_known_estimate_is_marked_incomplete_if_another_account_is_unknown(self):
        a,b=account(),account(number='4')
        rows=[entry(a,[('weekly',0,100)]),dict(entry(b,[]),status='UNKNOWN')]
        result=next_availability([a,b],rows,'codex',NOW)
        self.assertEqual(result['account'],'2');self.assertTrue(result['incomplete'])

    def test_agy_families_are_independent_and_each_family_waits_for_all_its_blockers(self):
        a=account('agy','current',single=True)
        row=entry(a,[('Gemini Models · 5h',0,100),('Gemini Models · Semanal',0,800),
                     ('Claude and GPT models · 5h',0,400),('Claude and GPT models · Semanal',20,900)])
        result=next_availability([a],[row],'agy',NOW)
        self.assertEqual(result['reset_at'],NOW+dt.timedelta(seconds=400))
        self.assertEqual(result['family'],'Claude and GPT models')
        row['windows'][2]['available_percent']=50
        self.assertEqual(next_availability([a],[row],'agy',NOW)['status'],'available')

    def test_opencode_never_turns_local_token_history_or_429_into_a_reset(self):
        a=account('opencode','current',single=True)
        row=entry(a,[('local 5h',0,50)])
        row['activity']={'windows':[{'load_percent':100}],'errors_429':99}
        self.assertEqual(next_availability([a],[row],'opencode',NOW)['status'],'unknown')

    def test_countdown_uses_current_clock_not_a_frozen_cache_and_never_rounds_to_zero_early(self):
        reset=NOW+dt.timedelta(seconds=90)
        self.assertEqual(countdown(reset,NOW),'00h 01m 30s')
        self.assertEqual(countdown(reset,NOW+dt.timedelta(seconds=30)),'00h 01m 00s')
        self.assertEqual(countdown(reset,NOW+dt.timedelta(seconds=89.5)),'00h 00m 01s')
        self.assertEqual(countdown(reset,NOW+dt.timedelta(seconds=100)),'00h 00m 00s')
        self.assertEqual(countdown(NOW+dt.timedelta(days=2,hours=3),NOW),'2d 03h 00m 00s')

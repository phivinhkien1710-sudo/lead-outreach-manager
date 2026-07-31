import unittest
from datetime import datetime

from lead_outreach_manager.services.outreach_batches import compute_staggered_send_after


class TestComputeStaggeredSendAfter(unittest.TestCase):
	def test_spacing_matches_rate_limit(self):
		start = datetime(2026, 7, 14, 9, 0, 0)
		# rate_limit_per_hour=60 -> 60s apart, business hours off so no rolling
		first = compute_staggered_send_after(0, start, rate_limit_per_hour=60, business_hours_only=False)
		second = compute_staggered_send_after(1, start, rate_limit_per_hour=60, business_hours_only=False)
		third = compute_staggered_send_after(2, start, rate_limit_per_hour=60, business_hours_only=False)

		self.assertEqual(first, start)
		self.assertEqual((second - first).total_seconds(), 60)
		self.assertEqual((third - second).total_seconds(), 60)

	def test_rolls_forward_past_business_hours_end(self):
		# start_after itself is within business hours; a later index pushes
		# past business_hours_end (18) and must roll to next day's start (9).
		start = datetime(2026, 7, 14, 17, 55, 0)
		result = compute_staggered_send_after(
			2, start, rate_limit_per_hour=6, business_hours_only=True, business_hours_start=9, business_hours_end=18,
		)
		# 2 * (3600/6)=1200s -> 17:55 + 20min = 18:15, past business_hours_end
		self.assertEqual(result, datetime(2026, 7, 15, 9, 0, 0))

	def test_rolls_forward_before_business_hours_start(self):
		start = datetime(2026, 7, 14, 3, 0, 0)
		result = compute_staggered_send_after(
			0, start, rate_limit_per_hour=10, business_hours_only=True, business_hours_start=9, business_hours_end=18,
		)
		self.assertEqual(result, datetime(2026, 7, 14, 9, 0, 0))

	def test_business_hours_only_false_never_rolls(self):
		start = datetime(2026, 7, 14, 23, 0, 0)
		result = compute_staggered_send_after(5, start, rate_limit_per_hour=60, business_hours_only=False)
		self.assertEqual(result, datetime(2026, 7, 14, 23, 5, 0))

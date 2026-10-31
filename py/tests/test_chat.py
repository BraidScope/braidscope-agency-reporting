# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.hubspot import hubspot_direct_intent_for_question
from app.klaviyo import (
    build_klaviyo_tool_arguments,
    klaviyo_direct_intent_for_question,
    select_klaviyo_tool_name,
)
from app.main import (
    AuthUser,
    ChatRequest,
    build_mcp_tools,
    get_chat_configuration,
    load_client_context,
    resolve_direct_tool_result,
    stream_chat_response,
    verify_bearer_token,
)
from app.chat_service import (
    build_final_input_text,
    detected_tool_error_message,
    direct_tool_fallback_text,
    is_non_answer_provider_text,
)
from app.codex import CODEX_MODEL_LINE_RE
from app import google_ads_dashboard
from app.google_ads_dashboard import resolve_google_ads_customer
from app.mcp_orchestrator import resolve_google_ads_customer_id as mcp_resolve_google_ads_customer_id
from app.klaviyo import build_klaviyo_metric_aggregate_arguments
import httpx
from app.mcp_orchestrator import (
    PLATFORMS,
    extract_date_range_from_question,
    find_metric_for_question,
    find_candidate_metrics_for_question,
    select_active_klaviyo_metric,
    query_wants_metric_performance,
    choose_klaviyo_metric_aggregate_call,
    choose_klaviyo_report_call,
    klaviyo_requested_statistics,
    klaviyo_wants_generic_performance,
    klaviyo_wants_ranking_by_flow_or_campaign,
    klaviyo_report_scope,
    klaviyo_timeframe_key_for_question,
    _mcp_post_with_retry,
    choose_platform_with_ai,
    choose_tool_with_ai,
    fallback_platform,
    ga4_tool_call,
    normalize_arguments,
    select_best_semantic_tool,
    selected_tool_is_usable,
    tool_matches_question,
    tool_is_read_only,
)


class ChatConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_configuration_is_not_ready_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            configuration = await get_chat_configuration("client-1", ["google_ads", "klaviyo"])

        self.assertFalse(configuration["ready"])
        self.assertEqual(
            configuration["missing_integrations"],
            ["google_ads", "klaviyo"],
        )

    async def test_tools_are_limited_to_connected_configured_sources(self):
        environment = {
            "GOOGLE_ADS_MCP_URL": "https://google.example/mcp",
            "GOOGLE_ADS_MCP_ACCESS_TOKEN": "google-token",
            "KLAVIYO_MCP_ACCESS_TOKEN": "klaviyo-token",
        }
        with patch.dict(os.environ, environment, clear=True):
            tools = await build_mcp_tools("client-1", ["klaviyo"])

        self.assertEqual([tool["server_label"] for tool in tools], ["klaviyo"])
        self.assertEqual(tools[0]["require_approval"], "never")

    async def test_hubspot_tool_is_added_when_connected_and_token_exists(self):
        environment = {
            "HUBSPOT_MCP_ACCESS_TOKEN": "hubspot-token",
        }
        with patch.dict(os.environ, environment, clear=True):
            tools = await build_mcp_tools("client-1", ["hubspot_crm"])

        self.assertEqual([tool["server_label"] for tool in tools], ["hubspot_crm"])
        self.assertEqual(tools[0]["server_url"], "https://mcp.hubspot.com")


class ProviderAnswerGuardTests(unittest.TestCase):
    def test_rejects_codex_instruction_echo(self):
        self.assertTrue(
            is_non_answer_provider_text(
                "Understood. I’ll answer from the selected Braidscope client’s official MCP data "
                "and ask once if a missing date range would change the answer."
            )
        )

    def test_keeps_real_data_answer(self):
        self.assertFalse(
            is_non_answer_provider_text("The site had 12,430 users in the last 30 days.")
        )

    def test_rejects_got_it_instruction_echo(self):
        # Captured verbatim from a real Ask AI request on 2026-09-23:
        # question "How many users are active right now?", GA4 tool card
        # showed success (the MCP call had already returned real data), yet
        # this is what reached the answer body instead of a number.
        self.assertTrue(
            is_non_answer_provider_text(
                "Got it. I’ll answer only from connected official data tools for the "
                "selected Braidscope client, keep responses compact, and ask a structured "
                "clarification when the date range or metric would change the answer."
            )
        )

    def test_rejects_bare_ready_period_deflection(self):
        # Captured verbatim from a real Ask AI request on 2026-09-23: same
        # question, same GA4 tool-success card, yet another distinct
        # non-answer shape on a different run.
        self.assertTrue(
            is_non_answer_provider_text(
                "Ready. Ask about the connected marketing data, Klaviyo, or HubSpot."
            )
        )

    def test_rejects_ready_for_client_marketing_questions_deflection(self):
        # Captured verbatim from a real Ask AI request on 2026-09-23: "What
        # are my Klaviyo account details?" with the klaviyo account_details
        # tool card showing success, yet the model answered this generic
        # readiness line instead of the real account details the tool had
        # already returned.
        self.assertTrue(
            is_non_answer_provider_text("Ready for Shawn's marketing analytics questions.")
        )
        self.assertTrue(
            is_non_answer_provider_text("Ready for Acme Corp's marketing analytics questions.")
        )

    def test_ga4_realtime_empty_rows_is_a_real_zero_not_no_data(self):
        # Ground truth for this exact behavior, captured live on 2026-09-23
        # by calling this client's real GA4 MCP endpoint directly: a
        # zero-active-users realtime report has NO "rows" key at all (not an
        # empty list), matching the real GA4 dashboard showing 0 active
        # users at the same moment. This must render as a real answer, not
        # the more technical (if not literally wrong) "GA4 returned no data
        # rows for this request."
        result = {
            "server_label": "google_analytics_4",
            "selected_tool": "get_realtime_active_users",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"dimensionHeaders": [{"name": "country"}], '
                                '"metricHeaders": [{"name": "activeUsers", "type": "TYPE_INTEGER"}], '
                                '"kind": "analyticsData#runRealtimeReport"}'
                            ),
                        }
                    ]
                }
            },
        }
        self.assertEqual(
            direct_tool_fallback_text(result, "How many users are active right now?"),
            "There are currently 0 active users.",
        )

    def test_rejects_generic_mcp_openers(self):
        self.assertTrue(
            is_non_answer_provider_text(
                "I'm ready to analyze Shawn's connected data using read-only official MCP tools."
            )
        )
        self.assertTrue(
            is_non_answer_provider_text(
                "I'll use official MCP data for Shawn and keep all analysis read-only. "
                "What would you like me to check?"
            )
        )

    def test_rejects_raw_ga4_envelope(self):
        self.assertTrue(
            is_non_answer_provider_text(
                'kind: analyticsData#runRealtimeReport type: text; text: '
                '{"dimensionHeaders":[{"name":"country"}], '
                '"metricHeaders":[{"name":"activeUsers"}]}'
            )
        )

    def test_rejects_live_codex_no_access_refusals(self):
        # Captured verbatim (curly apostrophes included) from a real,
        # authenticated Codex/ChatGPT `codex exec` run on 2026-09-23 against
        # a prompt containing "There are currently 17 active users." as the
        # normalized MCP result - Codex refused to use it and produced these
        # instead, reproducibly, regardless of question wording. These must
        # never reach the user; the deterministic fallback exists exactly
        # for this reproduced failure mode.
        refusals = (
            "I can’t access Live Trace’s GA4 data in this chat: no GA4 analytics "
            "tools are available. I can’t provide analytics without data returned by "
            "those tools.",
            "No data was returned.",
            "I don’t have access to live user activity data in this chat, so I "
            "can’t determine how many users are active right now.",
            "I can’t see the website’s analytics data in this workspace, so I "
            "can’t give a reliable visitor count. Connect the analytics source (such "
            "as GA4, Mixpanel, or PostHog), and I can look up the last 30 days.",
            "I can’t determine the current active user count from the available "
            "context. Which product or analytics dashboard should I check?",
        )
        for refusal in refusals:
            with self.subTest(refusal=refusal[:50]):
                self.assertTrue(is_non_answer_provider_text(refusal))

    def test_keeps_partial_answer_with_real_number_despite_a_caveat(self):
        self.assertFalse(
            is_non_answer_provider_text(
                "I can’t break this down by device, but there were 12,430 total users."
            )
        )

    def test_rejects_bare_clarifying_question_without_official_mcp_framing(self):
        # Captured live from the same authenticated Codex session as the
        # no-access refusals above, on an IDENTICAL prompt (same embedded
        # answer, "There are currently 17 active users.") that on other runs
        # produced the no-access-style refusals - proof the model's failure
        # mode is not one fixed phrase.
        self.assertTrue(
            is_non_answer_provider_text("What would you like to analyze for Live Trace?")
        )

    def test_rejects_clarifying_question_mentioning_ga4_by_name(self):
        # Also captured live, same session/prompt as above: "Google Analytics
        # 4" and "GA4" contain a literal digit ("4") that is the platform's
        # own product name, not a metric value - this must still be rejected,
        # not waved through by a naive "response contains a digit" guard.
        self.assertTrue(
            is_non_answer_provider_text(
                "Ready to analyze Live Trace’s Google Analytics 4 data. "
                "What would you like to know?"
            )
        )
        self.assertTrue(
            is_non_answer_provider_text("I can’t access GA4 in this chat right now.")
        )

    def test_rejects_bare_ready_to_analyze_statement_with_no_question_mark(self):
        # Also captured live, same session/prompt: a third distinct
        # non-deterministic refusal shape for the identical prompt (the
        # other two runs produced a no-access refusal and a correct answer
        # respectively) - a flat declarative sentence with no trailing "?",
        # so it also would have missed the bare-clarifying-question check.
        self.assertTrue(
            is_non_answer_provider_text(
                "Ready to analyze Live Trace’s Google Analytics 4 data."
            )
        )

    def test_keeps_real_answer_that_offers_a_followup_question(self):
        self.assertFalse(
            is_non_answer_provider_text(
                "Revenue was $12,430 for the last 30 days – want a daily breakdown?"
            )
        )

    def test_rejects_client_specific_ready_to_help_message(self):
        self.assertTrue(
            is_non_answer_provider_text(
                "Ready to help with Shawn's marketing analytics using only official MCP "
                "data and read-only analysis."
            )
        )

    def test_formats_ga4_report_rows_for_fallback(self):
        result = {
            "server_label": "google_analytics_4",
            "selected_tool": "get_traffic_overview",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"metricHeaders":[{"name":"totalUsers"}],'
                                '"rows":[{"metricValues":[{"value":"1234"}]}]}'
                            ),
                        }
                    ]
                }
            },
        }
        self.assertIn("total users: 1,234", direct_tool_fallback_text(result))

    def test_formats_ga4_rate_and_duration_metrics_as_human_readable(self):
        result = {
            "server_label": "google_analytics_4",
            "selected_tool": "get_traffic_overview",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"metricHeaders":['
                                '{"name":"engagementRate"},'
                                '{"name":"averageSessionDuration"},'
                                '{"name":"bounceRate"}'
                                '],'
                                '"rows":[{"metricValues":['
                                '{"value":"0.23333333333333334"},'
                                '{"value":"123.53906246666665"},'
                                '{"value":"0.7666666666666672"}'
                                ']}]}'
                            ),
                        }
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertIn("engagement rate: 23.3%", text)
        self.assertIn("avg. session duration: 2m 4s", text)
        self.assertIn("bounce rate: 76.7%", text)
        self.assertNotIn("0.23333333333333334", text)

    def test_formats_realtime_zero_as_a_valid_answer(self):
        result = {
            "server_label": "google_analytics_4",
            "selected_tool": "get_realtime_active_users",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"metricHeaders":[{"name":"activeUsers"}],'
                                '"rows":[{"metricValues":[{"value":"0"}]}]}'
                            ),
                        }
                    ]
                }
            },
        }
        self.assertEqual(
            direct_tool_fallback_text(result, "How many users are active right now?"),
            "There are currently 0 active users.",
        )
        prompt = build_final_input_text(
            ChatRequest(
                question="How many users are active right now?",
                client_id="client-1",
                client_name="Test",
                connected_sources=["google_analytics_4"],
            ),
            result,
        )
        self.assertIn("There are currently 0 active users.", prompt)
        self.assertNotIn("analyticsData#runRealtimeReport", prompt)

    def test_sums_realtime_dimension_rows_without_inventing_values(self):
        result = {
            "server_label": "google_analytics_4",
            "selected_tool": "get_realtime_active_users",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"metricHeaders":[{"name":"activeUsers"}],'
                                '"rows":[{"metricValues":[{"value":"15"}]},'
                                '{"metricValues":[{"value":"7"}]}]}'
                            ),
                        }
                    ]
                }
            },
        }
        self.assertEqual(
            direct_tool_fallback_text(result, "How many users are active right now?"),
            "There are currently 22 active users.",
        )

    def test_formats_empty_ga4_realtime_response_without_raw_json(self):
        result = {
            "server_label": "google_analytics_4",
            "selected_tool": "get_realtime_active_users",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"kind":"analyticsData#runRealtimeReport",'
                                '"dimensionHeaders":[{"name":"country"}],'
                                '"metricHeaders":[{"name":"activeUsers","type":"TYPE_INTEGER"}]}'
                            ),
                        }
                    ]
                }
            },
        }
        fallback = direct_tool_fallback_text(result)
        self.assertIn("GA4 returned no data rows", fallback)
        self.assertNotIn("analyticsData#runRealtimeReport", fallback)

    def test_ga4_historical_users_use_historical_overview(self):
        tools = [
            {"name": "get_traffic_overview"},
            {"name": "get_realtime_active_users"},
        ]
        self.assertEqual(
            ga4_tool_call("How many users visited the website in the last 30 days?", tools),
            {"tool_name": "get_traffic_overview", "arguments": {}},
        )

    def test_ga4_realtime_users_use_realtime_report(self):
        tools = [
            {"name": "get_traffic_overview"},
            {"name": "get_realtime_active_users"},
        ]
        self.assertEqual(
            ga4_tool_call("How many users are active right now?", tools),
            {"tool_name": "get_realtime_active_users", "arguments": {}},
        )

    def test_ga4_total_traffic_uses_overview_not_list_properties(self):
        # Captured live on 2026-09-23: this exact question matched none of
        # get_traffic_overview's old keywords, fell through to the
        # AI-assisted planner, which picked "list_properties" (lists
        # connected GA4 properties, not a traffic report) instead.
        tools = [
            {"name": "get_traffic_overview"},
            {"name": "get_traffic_sources"},
            {"name": "list_properties"},
        ]
        self.assertEqual(
            ga4_tool_call("What's the total traffic this month?", tools),
            {"tool_name": "get_traffic_overview", "arguments": {}},
        )

    def test_ga4_traffic_sources_still_wins_over_total_traffic_overlap(self):
        # Guards against the new "total traffic" keyword stealing a
        # traffic-SOURCES question that happens to also mention a date range.
        tools = [
            {"name": "get_traffic_overview"},
            {"name": "get_traffic_sources"},
        ]
        self.assertEqual(
            ga4_tool_call("What are the top traffic sources in the last 30 days?", tools),
            {"tool_name": "get_traffic_sources", "arguments": {}},
        )

    def test_ga4_landing_pages_question_does_not_get_stolen_by_overview(self):
        # Captured live on 2026-09-23: "top 5 landing pages by sessions"
        # mentions "sessions", so the old ordering (overview checked first)
        # matched get_traffic_overview and returned the same site-wide KPI
        # answer as every other question, ignoring "landing pages" entirely.
        tools = [
            {"name": "get_traffic_overview"},
            {"name": "get_landing_pages"},
        ]
        self.assertEqual(
            ga4_tool_call("What were our top 5 landing pages by sessions last 30 days?", tools),
            {"tool_name": "get_landing_pages", "arguments": {}},
        )

    def test_ga4_channel_question_does_not_get_stolen_by_overview(self):
        # Same bug, different question: "engaged sessions" overlaps
        # get_traffic_overview's "sessions" keyword and used to win before
        # "traffic channel" ever got a chance to route to traffic_sources.
        tools = [
            {"name": "get_traffic_overview"},
            {"name": "get_traffic_sources"},
        ]
        self.assertEqual(
            ga4_tool_call("Which traffic channel drove the most engaged sessions this quarter?", tools),
            {"tool_name": "get_traffic_sources", "arguments": {}},
        )

    def test_ga4_singular_channel_question_routes_to_traffic_sources(self):
        # Captured live on 2026-09-23: "Which channel is bringing the most
        # engaged sessions?" uses singular "channel", not "channels" or
        # "traffic channel", so it fell through the get_traffic_sources
        # check to the generic get_traffic_overview fallback (matched via
        # "sessions") and returned the same site-wide KPI answer as an
        # unrelated question about total sessions and new users.
        tools = [
            {"name": "get_traffic_overview"},
            {"name": "get_traffic_sources"},
        ]
        self.assertEqual(
            ga4_tool_call("Which channel is bringing the most engaged sessions?", tools),
            {"tool_name": "get_traffic_sources", "arguments": {}},
        )

    def test_ga4_device_breakdown_question_does_not_get_stolen_by_overview(self):
        tools = [
            {"name": "get_traffic_overview"},
            {"name": "get_device_breakdown"},
        ]
        self.assertEqual(
            ga4_tool_call("Compare sessions and bounce rate by device category this month", tools),
            {"tool_name": "get_device_breakdown", "arguments": {}},
        )


class GA4RawResultSummaryTests(unittest.TestCase):
    """Both fixtures below are real payloads, captured live on 2026-09-23 by
    calling this client's actual GA4 MCP endpoint directly - not guesses at
    the shape. Both previously leaked raw, unparsed JSON to the user because
    nothing upstream knew how to summarize these two shapes."""

    def test_list_properties_gets_a_real_summary_not_raw_json(self):
        result = {
            "server_label": "google_analytics_4",
            "selected_tool": "list_properties",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"properties": [{"property_id": "421596606", '
                                '"property": "properties/421596606", "display_name": '
                                '"learning-firebabase", "account": "Default Account for Firebase"}, '
                                '{"property_id": "488212228", "property": "properties/488212228", '
                                '"display_name": "peace-player", "account": "Default Account for Firebase"}]}'
                            ),
                        }
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertIn("learning-firebabase (421596606)", text)
        self.assertIn("peace-player (488212228)", text)
        self.assertNotIn("type: text", text)
        self.assertNotIn('"properties"', text)

    def test_empty_traffic_overview_gets_a_clean_message_not_raw_field(self):
        result = {
            "server_label": "google_analytics_4",
            "selected_tool": "get_traffic_overview",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"metadata": {"currencyCode": "USD", "timeZone": "Etc/GMT-5"}, '
                                '"kind": "analyticsData#runReport"}'
                            ),
                        }
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertEqual(text, "GA4 returned no data for this request.")
        self.assertNotIn("kind:", text)

    def test_raw_json_string_never_appears_as_a_flat_field(self):
        # General safety-net check on the two lower-level helpers directly,
        # not just through the higher-level fallback text.
        from app.chat_service import compact_json_preview, summarize_aggregate_record

        record = {"type": "text", "text": '{"foo": "bar"}'}
        self.assertNotIn("text:", summarize_aggregate_record(record))
        self.assertNotIn("text:", compact_json_preview(record))


class GoogleAdsRawResultSummaryTests(unittest.TestCase):
    """Confirmed live on 2026-09-23: get_campaign_performance's real
    response shape - Google Ads searchStream rows with every field nested
    inside its resource group ("campaign", "metrics", ...) - defeated every
    flat-field summarizer and surfaced only "type: text" as the answer."""

    def test_campaign_performance_gets_a_real_summary_not_type_text(self):
        result = {
            "server_label": "google_ads",
            "selected_tool": "get_campaign_performance",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "results": [
                                        {
                                            "campaign": {
                                                "id": "111",
                                                "name": "Search - Brand",
                                                "status": "ENABLED",
                                            },
                                            "metrics": {
                                                "impressions": "1204",
                                                "clicks": "87",
                                                "ctr": "0.07225913621262458",
                                                "costMicros": "1345670",
                                                "averageCpc": "15468620",
                                                "conversions": "4",
                                            },
                                        }
                                    ]
                                }
                            ),
                        }
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertNotIn("type: text", text)
        self.assertIn("Search - Brand", text)
        self.assertIn("clicks: 87", text)
        self.assertIn("CTR: 7.2%", text)
        self.assertIn("cost: $1.35", text)
        self.assertIn("avg. CPC: $15.47", text)

    def test_empty_campaign_results_gets_a_clean_message(self):
        result = {
            "server_label": "google_ads",
            "selected_tool": "get_campaign_performance",
            "result": {
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps({"results": []})},
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertEqual(text, "Google Ads returned no rows for this request.")


class MetaAdsRawResultSummaryTests(unittest.TestCase):
    """Meta's Graph API insights shape reports conversions/revenue/ROAS as
    arrays of {"action_type", "value"} pairs on the row - never as top-level
    scalar fields. Left to the generic compact_json_preview fallback (which
    skips any nested list/dict field), every purchase/lead/ROAS figure a real
    insights row contains would be silently dropped - the exact "must not
    drop a real number" failure the grounding rules are meant to prevent."""

    def test_campaign_performance_surfaces_actions_and_roas(self):
        result = {
            "server_label": "meta_ads",
            "selected_tool": "get_campaign_performance",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "data": [
                                    {
                                        "campaign_name": "Prospecting - US",
                                        "spend": "412.55",
                                        "impressions": "50213",
                                        "clicks": "612",
                                        "ctr": "1.219",
                                        "cpc": "0.674",
                                        "actions": [
                                            {"action_type": "purchase", "value": "12"},
                                            {"action_type": "link_click", "value": "600"},
                                        ],
                                        "action_values": [
                                            {"action_type": "purchase", "value": "450.00"},
                                        ],
                                        "purchase_roas": [
                                            {"action_type": "purchase", "value": "3.6"},
                                        ],
                                    }
                                ]
                            }),
                        }
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertIn("Prospecting - US", text)
        self.assertIn("spend: $412.55", text)
        self.assertIn("ctr: 1.22%", text)
        self.assertIn("purchase: 12", text)
        self.assertIn("purchase value: $450.00", text)
        self.assertIn("purchase ROAS: 3.60x", text)
        self.assertNotIn("type: text", text)

    def test_zero_spend_is_preserved_not_reported_as_no_data(self):
        result = {
            "server_label": "meta_ads",
            "selected_tool": "get_campaign_performance",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "data": [
                                    {"campaign_name": "Paused - Winter", "spend": "0", "impressions": "0", "clicks": "0"},
                                ]
                            }),
                        }
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertIn("spend: $0.00", text)
        self.assertNotIn("no rows", text.lower())

    def test_empty_insights_gets_a_clean_message(self):
        result = {
            "server_label": "meta_ads",
            "selected_tool": "get_campaign_performance",
            "result": {
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps({"data": []})},
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertEqual(text, "Meta Ads returned no rows for this request.")


class HubspotRawResultSummaryTests(unittest.TestCase):
    """HubSpot's CRM v3 API - the shape its official MCP server proxies -
    nests every real field one level down inside "properties"
    ({"id", "properties": {...}}), never as a top-level scalar. Left to the
    generic compact_json_preview fallback, "properties" is a dict and is
    skipped entirely, so only id/createdAt/updatedAt/archived would ever
    surface - never the contact's name/email or the deal's amount/stage the
    user actually asked about."""

    def test_contact_search_extracts_properties(self):
        result = {
            "server_label": "hubspot_crm",
            "selected_tool": "search_contacts",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "results": [
                                    {
                                        "id": "512",
                                        "properties": {
                                            "email": "jane@example.com",
                                            "firstname": "Jane",
                                            "lastname": "Doe",
                                            "phone": "555-1234",
                                        },
                                    }
                                ]
                            }),
                        }
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertIn("jane@example.com", text)
        self.assertIn("firstname: Jane", text)
        self.assertIn("lastname: Doe", text)
        self.assertIn("phone: 555-1234", text)

    def test_deal_search_extracts_amount_and_stage(self):
        result = {
            "server_label": "hubspot_crm",
            "selected_tool": "search_deals",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "results": [
                                    {
                                        "id": "9",
                                        "properties": {
                                            "dealname": "Acme Renewal",
                                            "amount": "5000",
                                            "dealstage": "closedwon",
                                        },
                                    }
                                ]
                            }),
                        }
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertIn("Acme Renewal", text)
        self.assertIn("amount: 5000", text)
        self.assertIn("dealstage: closedwon", text)

    def test_empty_results_gets_a_clean_message(self):
        result = {
            "server_label": "hubspot_crm",
            "selected_tool": "search_contacts",
            "result": {
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps({"results": []})},
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertEqual(text, "HubSpot returned no records for this request.")


class GoogleAdsHubspotResultsKeyCollisionTests(unittest.TestCase):
    """Google Ads and HubSpot's CRM v3 API both wrap their rows in a
    top-level "results" list, but with completely different row shapes.
    Before disambiguating on server_label, an empty HubSpot {"results": []}
    was misreported as "Google Ads returned no rows for this request." (the
    Google Ads summarizer is checked first and used to claim any empty
    "results" list unconditionally), and a non-empty HubSpot response with
    real records was misreported the same way once every row failed to
    match a Google Ads row shape."""

    def test_empty_hubspot_results_is_not_reported_as_google_ads(self):
        result = {
            "server_label": "hubspot_crm",
            "selected_tool": "search_companies",
            "result": {
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps({"results": []})},
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertEqual(text, "HubSpot returned no records for this request.")
        self.assertNotIn("Google Ads", text)

    def test_empty_google_ads_results_still_reports_google_ads(self):
        result = {
            "server_label": "google_ads",
            "selected_tool": "get_campaign_performance",
            "result": {
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps({"results": []})},
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertEqual(text, "Google Ads returned no rows for this request.")

    def test_hubspot_records_are_not_swallowed_by_google_ads_summarizer(self):
        result = {
            "server_label": "hubspot_crm",
            "selected_tool": "search_companies",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "results": [
                                    {"id": "77", "properties": {"name": "Acme Inc", "domain": "acme.com"}},
                                ]
                            }),
                        }
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertNotEqual(text, "Google Ads returned no rows for this request.")
        self.assertIn("Acme Inc", text)
        self.assertIn("domain: acme.com", text)


class McpToolErrorDetectionTests(unittest.TestCase):
    """Captured live on 2026-09-23 against a real client's actual Google Ads
    account: get_campaign_performance returned HTTP 200 / JSON-RPC success,
    but the content was a genuine upstream Google Ads API error (403
    CUSTOMER_NOT_ENABLED), nested as a nested JSON string. Before this fix,
    nothing recognized that shape: the answer fell through to "type: text",
    and separately Codex burned a full 90-second exec timeout trying to
    interpret the error payload as if it were real campaign data."""

    def test_detects_real_google_ads_permission_error(self):
        result = {
            "server_label": "google_ads",
            "selected_tool": "get_campaign_performance",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "error": json.dumps(
                                        [
                                            {
                                                "error": {
                                                    "code": 403,
                                                    "message": "The caller does not have permission",
                                                    "status": "PERMISSION_DENIED",
                                                    "details": [
                                                        {
                                                            "@type": "type.googleapis.com/google.ads.googleads.v24.errors.GoogleAdsFailure",
                                                            "errors": [
                                                                {
                                                                    "errorCode": {
                                                                        "authorizationError": "CUSTOMER_NOT_ENABLED"
                                                                    },
                                                                    "message": (
                                                                        "The customer account can't be accessed "
                                                                        "because it is not yet enabled or has been "
                                                                        "deactivated."
                                                                    ),
                                                                }
                                                            ],
                                                        }
                                                    ],
                                                }
                                            }
                                        ]
                                    )
                                }
                            ),
                        }
                    ]
                }
            },
        }
        message = detected_tool_error_message(result)
        self.assertIsNotNone(message)
        self.assertIn("Google Ads couldn't complete this request", message)
        self.assertIn("not yet enabled or has been deactivated", message)
        self.assertNotIn("type: text", message)
        self.assertNotIn("PERMISSION_DENIED", message)

        fallback_text = direct_tool_fallback_text(result)
        self.assertEqual(fallback_text, message)

    def test_detects_simple_error_dict(self):
        result = {
            "server_label": "google_analytics_4",
            "selected_tool": "get_traffic_overview",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"error": "GA4 access token is missing. Reconnect Google Analytics 4 and try again."}
                            ),
                        }
                    ]
                }
            },
        }
        message = detected_tool_error_message(result)
        self.assertEqual(
            message,
            "Google Analytics 4 couldn't complete this request: GA4 access token is missing. "
            "Reconnect Google Analytics 4 and try again.",
        )

    def test_detects_bare_unenveloped_error_dict(self):
        # execute_mcp_tool() itself returns a bare {"error": "..."} dict
        # directly (missing token, or - as of the Google Ads
        # customer-resolution fix - "no usable customer found") that is
        # NEVER wrapped in the standard MCP content-array format real tool
        # calls use. Confirmed live on 2026-09-23: without a direct check
        # for this shape, it fell through to the generic flat-field dump and
        # surfaced as a raw "error: <message>" line instead of a clean,
        # source-attributed sentence.
        result = {
            "server_label": "google_ads",
            "selected_tool": "get_campaign_performance",
            "result": {"error": "No usable Google Ads customer found for this client."},
        }
        message = detected_tool_error_message(result)
        self.assertEqual(
            message,
            "Google Ads couldn't complete this request: No usable Google Ads customer found for this client.",
        )
        fallback_text = direct_tool_fallback_text(result)
        self.assertEqual(fallback_text, message)
        self.assertNotIn("error:", fallback_text)

    def test_valid_data_never_triggers_error_detection(self):
        result = {
            "server_label": "google_ads",
            "selected_tool": "get_campaign_performance",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "results": [
                                        {
                                            "campaign": {"name": "Search - Brand"},
                                            "metrics": {"clicks": "87"},
                                        }
                                    ]
                                }
                            ),
                        }
                    ]
                }
            },
        }
        self.assertIsNone(detected_tool_error_message(result))

    def test_detects_klaviyo_json_api_error_shape(self):
        # Captured live on 2026-09-23: a real, malformed Klaviyo tool call
        # (the AI planner had picked a wrong tool and supplied no
        # arguments) returned this JSON:API-style error shape instead of
        # the {"error": "..."} convention other platforms use. Before this
        # fix, the real message ("Company does not exist.") was buried
        # three levels deep inside a JSON-encoded "detail" string, and only
        # the bare outer "status: 400" field survived as the "answer".
        result = {
            "server_label": "klaviyo",
            "selected_tool": "list_agent_knowledge",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "data": {"errors": [{
                                    "id": "e2e70f2878c71cb8a2d7eb8070c30f31",
                                    "status": 400,
                                    "code": "invalid",
                                    "title": "Invalid input.",
                                    "detail": json.dumps({"detail": {"error": "Company does not exist."}}),
                                    "source": {"pointer": "/data"},
                                }]},
                                "status": 400,
                            }),
                        }
                    ],
                    "isError": True,
                }
            },
        }
        message = detected_tool_error_message(result)
        self.assertEqual(message, "Klaviyo couldn't complete this request: Company does not exist.")
        self.assertNotIn("status: 400", message)


class KlaviyoAccountDetailsSummaryTests(unittest.TestCase):
    """Fixture is the real payload captured live on 2026-09-23 by calling
    this client's actual Klaviyo MCP endpoint directly, after fixing the
    "model" argument bug that previously made this call fail validation
    entirely. Before this fix, the "account" resource type fell to the
    generic name/status/id branch (which found neither, since Klaviyo's real
    account name lives three levels deep at
    attributes.contact_information.organization_name) and produced
    "(No name)", while a separate un-suppressed generic flat-dump appended a
    stray "1. type: text" line."""

    def test_account_details_extracts_real_nested_fields(self):
        result = {
            "server_label": "klaviyo",
            "selected_tool": "get_account_details",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "data": [{
                                    "type": "account",
                                    "id": "SpwtQ3",
                                    "attributes": {
                                        "test_account": False,
                                        "contact_information": {
                                            "default_sender_name": "TOMA Domino",
                                            "organization_name": "TOMA Domino",
                                        },
                                        "industry": "Ecommerce, Toys & Hobbies",
                                        "timezone": "US/Eastern",
                                        "preferred_currency": "USD",
                                    },
                                }]
                            }),
                        }
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertIn("TOMA Domino", text)
        self.assertIn("Industry: Ecommerce, Toys & Hobbies", text)
        self.assertIn("Timezone: US/Eastern", text)
        self.assertIn("Currency: USD", text)
        self.assertNotIn("(No name)", text)
        self.assertNotIn("type: text", text)


class KlaviyoMetricAggregateSummaryTests(unittest.TestCase):
    """Fixture is a trimmed version of the real payload captured live on
    2026-09-23 by calling this client's actual query_metric_aggregates tool
    directly (a "Placed Order" count over the last 31 days, all real zeros -
    this account had no orders in that window). query_metric_aggregates
    returns a single OBJECT, not a list, so summarize_klaviyo_collection()
    (which only handles list-shaped "data") never applied, and this fell
    through to the generic flat-field fallback, surfacing the raw envelope's
    "type: metric-aggregate" discriminator as if it were the answer - a real
    zero silently misrepresented as a meaningless label instead of stated
    clearly."""

    def test_real_zero_valued_aggregate_is_reported_as_a_real_zero(self):
        result = {
            "server_label": "klaviyo",
            "selected_tool": "query_metric_aggregates",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "data": {
                                    "type": "metric-aggregate",
                                    "id": "2364393996045315841",
                                    "attributes": {
                                        "dates": [
                                            "2026-08-24T00:00:00+00:00",
                                            "2026-09-23T00:00:00+00:00",
                                        ],
                                        "data": [
                                            {
                                                "dimensions": [],
                                                "measurements": {"count": [0.0, 0.0]},
                                            }
                                        ],
                                    },
                                }
                            }),
                        }
                    ]
                }
            },
        }
        text = direct_tool_fallback_text(result)
        self.assertIn("Total — count: 0", text)
        self.assertIn("2026-08-24 to 2026-09-23", text)
        self.assertNotIn("type: metric-aggregate", text)

    def test_nonzero_measurements_are_summed_correctly(self):
        payload = {
            "data": {
                "type": "metric-aggregate",
                "attributes": {
                    "dates": ["2026-09-01T00:00:00+00:00", "2026-09-02T00:00:00+00:00"],
                    "data": [
                        {"dimensions": [], "measurements": {"count": [3.0, 4.0]}},
                    ],
                },
            }
        }
        from app.chat_service import summarize_klaviyo_metric_aggregate

        self.assertEqual(
            summarize_klaviyo_metric_aggregate(payload),
            "Klaviyo metric aggregate (2026-09-01 to 2026-09-02):\n- Total — count: 7",
        )


class KlaviyoReportSummaryTests(unittest.TestCase):
    """Fixtures are trimmed real payloads captured live on 2026-09-23 by
    calling this client's actual get_campaign_report/get_flow_report tools
    directly. Both previously fell through to the generic flat-field
    fallback and surfaced the raw envelope's own "type" discriminator
    ("type: campaign-values-report" / "type: flow-values-report") instead
    of the real report data."""

    def test_empty_results_is_a_clean_message_not_raw_type_field(self):
        from app.chat_service import summarize_klaviyo_report

        payload = {
            "data": {
                "type": "campaign-values-report",
                "id": "631a741e-cf65-445c-933c-5059feb05c8c",
                "attributes": {"results": []},
            },
            "links": {"self": "https://a.klaviyo.com/api/campaign-values-reports"},
        }
        text = summarize_klaviyo_report(payload)
        self.assertEqual(text, "Klaviyo returned no matching campaigns or flows for this report and period.")
        self.assertNotIn("type:", text)

    def test_flow_report_rows_extract_name_and_key_statistics(self):
        from app.chat_service import summarize_klaviyo_report

        payload = {
            "data": {
                "type": "flow-values-report",
                "attributes": {
                    "results": [
                        {
                            "groupings": {
                                "flow_id": "RDUWYF",
                                "flow_name": "Abandoned Cart Reminder - (SMS) By Waqar",
                                "flow_message_id": "RfHViY",
                            },
                            "statistics": {
                                "recipients": 2.0, "delivered": 2.0, "delivery_rate": 1.0,
                                "opens": 0.0, "open_rate": 0.0,
                                "clicks": 4.0, "clicks_unique": 2.0, "click_rate": 1.0,
                                "conversions": 0.0, "conversion_rate": 0.0,
                                "average_order_value": 0.0,
                            },
                        },
                        {
                            "groupings": {
                                "flow_id": "SxxLFi",
                                "flow_name": "Welcome Series - (SMS) By Waqar",
                                "flow_message_id": "UjXEbM",
                            },
                            "statistics": {
                                "recipients": 12.0, "delivered": 12.0, "delivery_rate": 1.0,
                                "clicks": 11.0, "clicks_unique": 7.0, "click_rate": 0.58333,
                                "conversions": 3.0, "conversion_rate": 0.25, "conversion_value": 150.0,
                            },
                        },
                    ]
                },
            }
        }
        text = summarize_klaviyo_report(payload)
        self.assertNotIn("type:", text)
        self.assertIn("Abandoned Cart Reminder - (SMS) By Waqar", text)
        self.assertIn("Welcome Series - (SMS) By Waqar", text)
        self.assertIn("click rate: 100.0%", text)
        self.assertIn("conversion value: $150.00", text)
        # Higher conversion_value row ranks first.
        self.assertLess(text.index("Welcome Series"), text.index("Abandoned Cart"))


class KlaviyoMetricPerformanceGateTests(unittest.TestCase):
    def test_natural_phrasing_without_the_word_metric_still_triggers(self):
        # Captured live on 2026-09-23: this exact phrasing - an ordinary way
        # to ask for what query_metric_aggregates answers - contained none
        # of the old gate's trigger words (no literal "metric"), so the
        # deterministic aggregate path was silently never attempted.
        self.assertTrue(query_wants_metric_performance("How many orders were placed in the last 30 days?"))
        self.assertTrue(query_wants_metric_performance("How much revenue did we make this month?"))

    def test_unrelated_questions_still_do_not_match(self):
        self.assertFalse(query_wants_metric_performance("Show me recent Klaviyo flows."))
        self.assertFalse(query_wants_metric_performance("What are my Klaviyo account details?"))

    def test_old_metric_plus_keyword_phrasing_still_matches(self):
        self.assertTrue(query_wants_metric_performance("Show Klaviyo performance for Opened Email metric"))

    def test_value_phrasing_without_quantity_words_still_triggers(self):
        # Confirmed live on 2026-09-24: "What's our Placed Order revenue
        # over the last 30 days?" - naming a real metric plus a time range,
        # the single most natural way to ask this - contains no quantity
        # phrase at all ("how much"/"how many"/...), only a value-asking
        # opener ("what's our"). Without this, the deterministic aggregate
        # path never even attempted a match, and the generic planner picked
        # an unrelated bulk-job tool from Klaviyo's real 122-tool catalog.
        self.assertTrue(query_wants_metric_performance("What's our Placed Order revenue over the last 30 days?"))
        self.assertTrue(query_wants_metric_performance("What was our email revenue last month?"))

    def test_value_phrasing_without_time_phrase_does_not_match(self):
        self.assertFalse(query_wants_metric_performance("What's our brand voice?"))


class KlaviyoGenericPerformanceGateTests(unittest.TestCase):
    """Confirmed live on 2026-09-24: "How did our last email campaign
    perform?" - an entirely ordinary, generic way to ask for a campaign
    report - names no specific rate statistic and asks for no ranking, so
    it matched neither klaviyo_requested_statistics nor
    klaviyo_wants_ranking_by_flow_or_campaign and
    choose_klaviyo_report_call() never even attempted a match, despite
    already defaulting its "statistics" argument to ["conversions",
    "conversion_rate"] for exactly this "no specific stat named" case."""

    def test_generic_campaign_performance_phrasing_detected(self):
        self.assertTrue(klaviyo_wants_generic_performance("How did our last email campaign perform?"))
        self.assertTrue(klaviyo_wants_generic_performance("How is our welcome flow performing?"))

    def test_performance_word_without_campaign_or_flow_does_not_match(self):
        self.assertFalse(klaviyo_wants_generic_performance("How did we perform overall?"))

    def test_campaign_mention_without_performance_word_does_not_match(self):
        self.assertFalse(klaviyo_wants_generic_performance("List my recent campaigns."))


class KlaviyoDuplicateMetricDisambiguationTests(unittest.IsolatedAsyncioTestCase):
    """Reproduces a real case captured live on 2026-09-23: this account has
    TWO metrics both literally named "Placed Order" - one from a
    since-abandoned Wix integration (zero activity in over a year) and one
    from the account's current, active Shopify integration (32 real orders
    in the last 30 days). Before this fix, "How many orders were placed in
    the last 30 days?" picked whichever one the API happened to list first
    (the dead Wix one) and answered "0", while the account's own Klaviyo
    dashboard showed real recent revenue for the same period."""

    def _metric(self, metric_id: str, name: str = "Placed Order") -> dict:
        return {"id": metric_id, "type": "metric", "attributes": {"name": name}}

    def test_finds_all_tied_candidates_not_just_the_first(self):
        metrics = [
            self._metric("wix-dead"),
            self._metric("other", "Opened Email"),
            self._metric("shopify-active"),
        ]
        candidates = find_candidate_metrics_for_question(
            "How many orders were placed in the last 30 days?", metrics,
        )
        self.assertEqual({c["id"] for c in candidates}, {"wix-dead", "shopify-active"})

    def test_find_metric_for_question_still_returns_a_single_metric(self):
        # Backward-compatible wrapper: still returns one metric (the first
        # candidate) for callers that only need the old single-result shape.
        metrics = [self._metric("only-one")]
        metric = find_metric_for_question("How many orders were placed?", metrics)
        self.assertEqual(metric["id"], "only-one")

    async def test_select_active_klaviyo_metric_prefers_the_one_with_real_data(self):
        candidates = [self._metric("wix-dead"), self._metric("shopify-active")]

        async def fake_execute_mcp_tool(platform, client_id, question, tool_name, arguments):
            metric_id = arguments["body"]["data"]["attributes"]["metric_id"]
            counts = [0.0, 0.0] if metric_id == "wix-dead" else [3.0, 5.0]
            return {
                "result": {"content": [{"type": "text", "text": json.dumps({
                    "data": {"type": "metric-aggregate", "attributes": {
                        "dates": ["2026-08-24T00:00:00+00:00", "2026-08-25T00:00:00+00:00"],
                        "data": [{"dimensions": [], "measurements": {"count": counts}}],
                    }},
                })}]}
            }

        with patch("app.mcp_orchestrator.execute_mcp_tool", new=AsyncMock(side_effect=fake_execute_mcp_tool)):
            selected = await select_active_klaviyo_metric(
                PLATFORMS["klaviyo"], "client-1", "query_metric_aggregates",
                candidates, "2026-08-24T00:00:00Z", "2026-08-25T23:59:59Z",
            )
        self.assertEqual(selected["id"], "shopify-active")

    async def test_select_active_klaviyo_metric_falls_back_to_first_if_all_dead(self):
        candidates = [self._metric("dead-a"), self._metric("dead-b")]

        async def fake_execute_mcp_tool(platform, client_id, question, tool_name, arguments):
            return {
                "result": {"content": [{"type": "text", "text": json.dumps({
                    "data": {"type": "metric-aggregate", "attributes": {
                        "dates": ["2026-08-24T00:00:00+00:00"],
                        "data": [{"dimensions": [], "measurements": {"count": [0.0]}}],
                    }},
                })}]}
            }

        with patch("app.mcp_orchestrator.execute_mcp_tool", new=AsyncMock(side_effect=fake_execute_mcp_tool)):
            selected = await select_active_klaviyo_metric(
                PLATFORMS["klaviyo"], "client-1", "query_metric_aggregates",
                candidates, "2026-08-24T00:00:00Z", "2026-08-24T23:59:59Z",
            )
        self.assertEqual(selected["id"], "dead-a")

    async def test_single_candidate_skips_probing_entirely(self):
        candidates = [self._metric("only-one")]
        with patch("app.mcp_orchestrator.execute_mcp_tool", new=AsyncMock()) as mocked:
            selected = await select_active_klaviyo_metric(
                PLATFORMS["klaviyo"], "client-1", "query_metric_aggregates",
                candidates, "2026-08-24T00:00:00Z", "2026-08-24T23:59:59Z",
            )
        self.assertEqual(selected["id"], "only-one")
        mocked.assert_not_awaited()


class McpPostRetryTests(unittest.IsolatedAsyncioTestCase):
    """Confirmed live on 2026-09-23: real Ask AI requests to Klaviyo's MCP
    server (mcp.klaviyo.com, reached over the public internet - unlike GA4/
    Google Ads, which proxy through this same machine's own localhost:8000)
    intermittently failed with a raw DNS ConnectError. That exception
    propagated out of the whole pre-fetch phase and was caught by a generic
    handler that showed "No relevant read-only MCP tool matched... try
    naming the data source" - misleading, since the platform and tool were
    already correctly resolved before the network call failed."""

    async def test_retries_transient_connect_error_then_succeeds(self):
        success_response = MagicMock()
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=[httpx.ConnectError("dns failed"), success_response])

        with patch("app.mcp_orchestrator.asyncio.sleep", new=AsyncMock()):
            response = await _mcp_post_with_retry(
                mock_client, "https://mcp.klaviyo.com/mcp", headers={}, json={"a": 1},
            )
        self.assertIs(response, success_response)
        self.assertEqual(mock_client.post.await_count, 2)

    async def test_gives_up_after_max_retries_and_raises(self):
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("dns failed"))

        with patch("app.mcp_orchestrator.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(httpx.ConnectError):
                await _mcp_post_with_retry(
                    mock_client, "https://mcp.klaviyo.com/mcp", headers={}, json={"a": 1},
                )
        self.assertEqual(mock_client.post.await_count, 3)  # 1 initial + 2 retries

    async def test_does_not_retry_a_real_http_error_response(self):
        # A real 4xx/5xx HTTP response is not a transport-level failure -
        # httpx doesn't raise for it here (raise_for_status is the caller's
        # job), so this should never be retried by this wrapper.
        error_response = MagicMock()
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=error_response)

        response = await _mcp_post_with_retry(
            mock_client, "https://mcp.klaviyo.com/mcp", headers={}, json={"a": 1},
        )
        self.assertIs(response, error_response)
        self.assertEqual(mock_client.post.await_count, 1)


class KlaviyoReportRoutingTests(unittest.IsolatedAsyncioTestCase):
    """Confirmed live on 2026-09-23: with all 122 real Klaviyo tools handed
    to the generic AI planner in one shot, it picked completely unrelated
    tools for these exact questions (get_bulk_suppress_profiles_jobs for a
    flow-revenue question, list_agent_knowledge for an email-open-rate
    question), and the existing semantic fallback scorer ranks
    get_campaign_report/get_flow_report near the bottom too (it penalizes
    "report"-named tools and tools with required fields). These tests cover
    the small helper functions choose_klaviyo_report_call is built from."""

    def test_open_rate_phrasing_detected(self):
        self.assertEqual(
            klaviyo_requested_statistics("What's our email open rate this month?"),
            ["open_rate"],
        )

    def test_click_rate_phrasing_detected(self):
        self.assertIn("click_rate", klaviyo_requested_statistics("How's our click rate doing?"))

    def test_unrelated_question_requests_no_statistics(self):
        self.assertEqual(klaviyo_requested_statistics("What are my Klaviyo account details?"), [])

    def test_ranking_phrasing_detected(self):
        self.assertTrue(
            klaviyo_wants_ranking_by_flow_or_campaign("Which flow drove the most revenue in the last 30 days?")
        )
        self.assertFalse(klaviyo_wants_ranking_by_flow_or_campaign("What's our email open rate this month?"))

    def test_report_scope_prefers_flow_when_only_flow_mentioned(self):
        self.assertEqual(klaviyo_report_scope("Which flow drove the most revenue?"), "flow")
        self.assertEqual(klaviyo_report_scope("What's our email open rate?"), "campaign")
        # Both mentioned - defaults to campaign rather than guessing.
        self.assertEqual(klaviyo_report_scope("Compare flow and campaign performance"), "campaign")

    def test_timeframe_key_maps_common_phrasings(self):
        self.assertEqual(klaviyo_timeframe_key_for_question("...in the last 30 days?"), "last_30_days")
        self.assertEqual(klaviyo_timeframe_key_for_question("...this month?"), "this_month")
        self.assertEqual(klaviyo_timeframe_key_for_question("no timeframe mentioned"), "last_30_days")

    async def test_open_rate_question_routes_to_campaign_report_with_real_metric(self):
        metrics_payload = {
            "data": [
                {"type": "metric", "id": "wix-dead", "attributes": {"name": "Placed Order"}},
                {"type": "metric", "id": "shopify-active", "attributes": {"name": "Placed Order"}},
            ]
        }
        aggregate_payloads = {
            "wix-dead": {"data": {"type": "metric-aggregate", "attributes": {
                "dates": ["2026-08-24T00:00:00+00:00"], "data": [{"dimensions": [], "measurements": {"count": [0.0]}}],
            }}},
            "shopify-active": {"data": {"type": "metric-aggregate", "attributes": {
                "dates": ["2026-08-24T00:00:00+00:00"], "data": [{"dimensions": [], "measurements": {"count": [5.0]}}],
            }}},
        }

        async def fake_execute_mcp_tool(platform, client_id, question, tool_name, arguments):
            if tool_name == "get_metrics":
                return {"result": {"content": [{"type": "text", "text": json.dumps(metrics_payload)}]}}
            metric_id = arguments["body"]["data"]["attributes"]["metric_id"]
            return {"result": {"content": [{"type": "text", "text": json.dumps(aggregate_payloads[metric_id])}]}}

        tools = [
            {"name": "get_campaign_report"},
            {"name": "get_flow_report"},
            {"name": "get_metrics"},
            {"name": "query_metric_aggregates"},
        ]
        with patch("app.mcp_orchestrator.execute_mcp_tool", new=AsyncMock(side_effect=fake_execute_mcp_tool)):
            call = await choose_klaviyo_report_call(
                "What's our email open rate this month?", PLATFORMS["klaviyo"], "client-1", tools,
            )
        self.assertEqual(call["tool_name"], "get_campaign_report")
        self.assertEqual(call["arguments"]["statistics"], ["open_rate"])
        self.assertEqual(call["arguments"]["conversion_metric_id"], "shopify-active")
        self.assertEqual(call["arguments"]["timeframe"], {"key": "this_month"})
        self.assertEqual(call["arguments"]["model"], "braidscope-ai")
        self.assertNotIn("group_by", call["arguments"])

    async def test_flow_revenue_ranking_question_routes_to_flow_report_grouped(self):
        metrics_payload = {"data": [{"type": "metric", "id": "the-metric", "attributes": {"name": "Placed Order"}}]}

        async def fake_execute_mcp_tool(platform, client_id, question, tool_name, arguments):
            return {"result": {"content": [{"type": "text", "text": json.dumps(metrics_payload)}]}}

        tools = [{"name": "get_campaign_report"}, {"name": "get_flow_report"}, {"name": "get_metrics"}]
        with patch("app.mcp_orchestrator.execute_mcp_tool", new=AsyncMock(side_effect=fake_execute_mcp_tool)):
            call = await choose_klaviyo_report_call(
                "Which flow drove the most revenue in the last 30 days?",
                PLATFORMS["klaviyo"], "client-1", tools,
            )
        self.assertEqual(call["tool_name"], "get_flow_report")
        self.assertEqual(call["arguments"]["group_by"], ["flow_id", "flow_name"])
        self.assertEqual(call["arguments"]["conversion_metric_id"], "the-metric")
        self.assertIn("conversions", call["arguments"]["statistics"])

    async def test_unrelated_question_does_not_match(self):
        tools = [{"name": "get_campaign_report"}, {"name": "get_flow_report"}, {"name": "get_metrics"}]
        call = await choose_klaviyo_report_call(
            "What are my Klaviyo account details?", PLATFORMS["klaviyo"], "client-1", tools,
        )
        self.assertIsNone(call)

    async def test_generic_how_did_it_perform_question_routes_to_campaign_report(self):
        # Confirmed live on 2026-09-24: this exact real question reached
        # Ask AI and, before this fix, picked get_bulk_import_profiles_jobs
        # from Klaviyo's real 122-tool catalog instead - completely
        # unrelated to campaign performance.
        metrics_payload = {"data": [{"type": "metric", "id": "the-metric", "attributes": {"name": "Placed Order"}}]}

        async def fake_execute_mcp_tool(platform, client_id, question, tool_name, arguments):
            return {"result": {"content": [{"type": "text", "text": json.dumps(metrics_payload)}]}}

        tools = [{"name": "get_campaign_report"}, {"name": "get_flow_report"}, {"name": "get_metrics"}]
        with patch("app.mcp_orchestrator.execute_mcp_tool", new=AsyncMock(side_effect=fake_execute_mcp_tool)):
            call = await choose_klaviyo_report_call(
                "How did our last email campaign perform?", PLATFORMS["klaviyo"], "client-1", tools,
            )
        self.assertIsNotNone(call)
        self.assertEqual(call["tool_name"], "get_campaign_report")
        self.assertEqual(call["arguments"]["statistics"], ["conversions", "conversion_rate"])
        self.assertEqual(call["arguments"]["conversion_metric_id"], "the-metric")
        self.assertNotIn("group_by", call["arguments"])


class KlaviyoMetricAggregateRoutingTests(unittest.IsolatedAsyncioTestCase):
    """Confirmed live on 2026-09-24: "What's our Placed Order revenue over
    the last 30 days?" - naming a real Klaviyo metric plus a time range -
    picked get_bulk_suppress_profiles_jobs from Klaviyo's real 122-tool
    catalog instead of query_metric_aggregates, because
    query_wants_metric_performance's old quantity-phrase requirement ("how
    much"/"how many"/...) doesn't cover "what's our X" phrasing."""

    async def test_revenue_value_question_routes_to_metric_aggregates(self):
        metrics_payload = {"data": [{"type": "metric", "id": "shopify-active", "attributes": {"name": "Placed Order"}}]}

        async def fake_execute_mcp_tool(platform, client_id, question, tool_name, arguments):
            return {"result": {"content": [{"type": "text", "text": json.dumps(metrics_payload)}]}}

        tools = [{"name": "get_metrics"}, {"name": "query_metric_aggregates"}]
        with patch("app.mcp_orchestrator.execute_mcp_tool", new=AsyncMock(side_effect=fake_execute_mcp_tool)):
            call = await choose_klaviyo_metric_aggregate_call(
                "What's our Placed Order revenue over the last 30 days?",
                PLATFORMS["klaviyo"], "client-1", tools,
            )
        self.assertIsNotNone(call)
        self.assertEqual(call["tool_name"], "query_metric_aggregates")
        self.assertEqual(call["resolved_metric"]["id"], "shopify-active")


class CodexActualModelTests(unittest.TestCase):
    """CODEX_MODEL_LINE_RE parses the plaintext session banner `codex exec`
    (no --json) writes to stderr before the turn output. Fixture text below is
    the real banner captured from a live, authenticated CODEX_HOME on
    2026-09-23 (`codex login status` -> "Logged in using ChatGPT"), not a
    guess at the format - see run_codex_exec's docstring in app/codex.py."""

    def test_extracts_model_from_real_default_run_banner(self):
        stderr = (
            "OpenAI Codex v0.155.1\n"
            "--------\n"
            "workdir: C:\\Users\\x\\AppData\\Local\\Braidscope\\codex-runners\\abc\\workspace\n"
            "model: gpt-6-luna\n"
            "provider: openai\n"
            "approval: never\n"
            "sandbox: read-only\n"
            "--------\n"
        )
        match = CODEX_MODEL_LINE_RE.search(stderr)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "gpt-6-luna")

    def test_codex_module_has_no_hardcoded_default_model_constant(self):
        # There used to be a module-level DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
        # here, which was never a real Codex default - just a frontend
        # placeholder (AiDrawer.jsx's old DEFAULT_MODELS.openai) persisted at
        # OAuth-login time and shown back as if it were authoritative. It has
        # been removed entirely from this module (byok.py's own, unrelated,
        # constant of the same name for the real OpenAI-API-key path is
        # untouched): the real default is only knowable by asking the
        # authenticated CLI itself, via discover_codex_models().
        import app.codex as codex_module

        self.assertFalse(hasattr(codex_module, "DEFAULT_OPENAI_MODEL"))

    def test_no_match_on_json_mode_output_without_banner(self):
        # `--json` mode (not used by run_codex_exec) emits JSONL events with
        # no plaintext banner at all - the regex must not false-positive on it.
        stderr = '{"type":"thread.started","thread_id":"01a0"}\n'
        self.assertIsNone(CODEX_MODEL_LINE_RE.search(stderr))


# Trimmed but structurally real fixture: same shape as `codex debug models`'
# actual JSON, captured live from an authenticated ChatGPT-backed session on
# 2026-09-23 (9 total entries in the real account catalog; kept here to 3 to
# stay readable while covering visibility="list" vs "hide" filtering and the
# reasoning/tool-mode fields discover_codex_models() reads).
LIVE_CODEX_MODELS_CATALOG_FIXTURE = {
    "models": [
        {
            "slug": "gpt-6-astra",
            "display_name": "GPT-6-Astra",
            "description": "Our most capable model for complex, demanding work.",
            "visibility": "list",
            "supported_in_api": True,
            "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}],
            "tool_mode": "code_mode_only",
        },
        {
            "slug": "gpt-5.5",
            "display_name": "GPT-5.5",
            "description": "",
            "visibility": "list",
            "supported_in_api": True,
            "supported_reasoning_levels": [{"effort": "medium"}],
            "tool_mode": None,
        },
        {
            "slug": "codex-auto-review",
            "display_name": "Codex Auto Review",
            "description": "",
            "visibility": "hide",
            "supported_in_api": True,
            "supported_reasoning_levels": [{"effort": "medium"}],
            "tool_mode": "code_mode_only",
        },
    ]
}


class CodexModelDiscoveryTests(unittest.TestCase):
    """get_codex_model_catalog() / verify_codex_model_selectable() /
    discover_codex_models() - built directly from the live investigation on
    2026-09-23: gpt-6-astra had visibility="list" and supported_in_api=true
    in a real account's catalog, yet `codex exec -m gpt-6-astra` was
    rejected outright; the same account's real live default, "gpt-6-luna",
    was not present in its catalog at all. Both are asserted here."""

    def test_catalog_filters_to_visibility_list_only(self):
        import tempfile

        from app.codex import get_codex_model_catalog

        fake_process = MagicMock()
        fake_process.communicate.return_value = (
            json.dumps(LIVE_CODEX_MODELS_CATALOG_FIXTURE).encode("utf-8"),
            b"",
        )
        fake_process.returncode = 0
        with tempfile.TemporaryDirectory() as codex_home:
            with patch("app.codex.subprocess.Popen", return_value=fake_process):
                catalog = get_codex_model_catalog(codex_home)

        ids = {entry["id"] for entry in catalog}
        self.assertEqual(ids, {"gpt-6-astra", "gpt-5.5"})
        self.assertNotIn("codex-auto-review", ids)  # visibility="hide"

        astra = next(entry for entry in catalog if entry["id"] == "gpt-6-astra")
        self.assertTrue(astra["reasoning_support"])
        self.assertTrue(astra["tool_support"])
        sol = next(entry for entry in catalog if entry["id"] == "gpt-5.5")
        self.assertFalse(sol["tool_support"])  # tool_mode was None in the fixture

    def test_catalog_returns_empty_list_never_raises_on_cli_failure(self):
        import tempfile

        from app.codex import get_codex_model_catalog

        fake_process = MagicMock()
        fake_process.communicate.return_value = (b"", b"unknown subcommand 'debug'")
        fake_process.returncode = 2
        with tempfile.TemporaryDirectory() as codex_home:
            with patch("app.codex.subprocess.Popen", return_value=fake_process):
                catalog = get_codex_model_catalog(codex_home)
        self.assertEqual(catalog, [])

    def test_verify_returns_false_on_live_rejection(self):
        # This is the exact live-confirmed gpt-6-astra case: catalog-listed,
        # supported_in_api=true, still rejected by a real ChatGPT-account
        # session.
        from app.codex import verify_codex_model_selectable

        with patch(
            "app.codex._codex_exec_raw",
            return_value=(
                1,
                "",
                'ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error",'
                '"message":"The \'gpt-6-astra\' model is not supported when using Codex with '
                'a ChatGPT account."}}',
            ),
        ):
            self.assertFalse(verify_codex_model_selectable("C:/fake/codex-home", "gpt-6-astra"))

    def test_verify_returns_true_on_live_acceptance(self):
        from app.codex import verify_codex_model_selectable

        with patch("app.codex._codex_exec_raw", return_value=(0, "ok", "model: gpt-5.5\n")):
            self.assertTrue(verify_codex_model_selectable("C:/fake/codex-home", "gpt-5.5"))

    def test_verify_returns_none_not_false_on_empty_output_failure(self):
        # This is the real, live-confirmed rate-limit/throttling pattern:
        # running discover_codex_models() for a real account on 2026-09-23,
        # 6 of 7 verification calls failed with a non-zero return code and
        # COMPLETELY EMPTY stdout/stderr, in well under a second each
        # (vs 10+ seconds for a real decision) - a fundamentally different
        # signature from a genuine rejection (which always produced a real
        # banner and a specific error message). Treating this as False
        # instead of None permanently excluded 6 real, valid models from
        # that client's list.
        from app.codex import verify_codex_model_selectable

        with patch("app.codex._codex_exec_raw", return_value=(1, "", "")):
            self.assertIsNone(verify_codex_model_selectable("C:/fake/codex-home", "gpt-5.6-luna"))

    def test_verify_returns_none_not_false_on_unrelated_error(self):
        # A real failure that isn't the specific "not supported" rejection
        # (e.g. a transient network error) must also not be recorded as a
        # confirmed rejection.
        from app.codex import verify_codex_model_selectable

        with patch("app.codex._codex_exec_raw", return_value=(1, "", "error sending request for url ...")):
            self.assertIsNone(verify_codex_model_selectable("C:/fake/codex-home", "gpt-5.6-luna"))

    def test_verify_returns_none_on_timeout(self):
        from app.codex import verify_codex_model_selectable

        with patch("app.codex._codex_exec_raw", side_effect=ValueError("Codex took too long to respond.")):
            self.assertIsNone(verify_codex_model_selectable("C:/fake/codex-home", "gpt-5.6-luna"))

    def test_discover_exposes_live_default_even_when_absent_from_catalog(self):
        # The exact live-confirmed mismatch: real default "gpt-6-luna" is not
        # among the 9 slugs a real account's own `codex debug models` lists.
        from app.codex import CodexExecResult, discover_codex_models

        with patch("app.codex.get_codex_model_catalog", return_value=[
            {"id": "gpt-6-astra", "display_name": "GPT-6-Astra", "description": "",
             "reasoning_support": True, "tool_support": True},
            {"id": "gpt-5.5", "display_name": "GPT-5.5", "description": "",
             "reasoning_support": True, "tool_support": False},
        ]), patch(
            "app.codex.run_codex_exec",
            return_value=CodexExecResult(text="ok", actual_model="gpt-6-luna"),
        ), patch(
            "app.codex.verify_codex_model_selectable",
            side_effect=lambda home, model_id: model_id != "gpt-6-astra",
        ):
            models = discover_codex_models("C:/fake/codex-home")

        by_id = {entry["id"]: entry for entry in models}
        self.assertIn("gpt-6-luna", by_id)
        self.assertTrue(by_id["gpt-6-luna"]["default"])
        self.assertTrue(by_id["gpt-6-luna"]["supported"])
        self.assertFalse(by_id["gpt-6-astra"]["supported"])
        self.assertFalse(by_id["gpt-6-astra"]["default"])
        self.assertTrue(by_id["gpt-5.5"]["supported"])

    def test_discover_marks_all_unverified_when_default_detection_fails(self):
        from app.codex import discover_codex_models

        with patch("app.codex.get_codex_model_catalog", return_value=[
            {"id": "gpt-5.5", "display_name": "GPT-5.5", "description": "",
             "reasoning_support": True, "tool_support": False},
        ]), patch("app.codex.run_codex_exec", side_effect=ValueError("Codex CLI is no longer authenticated")):
            models = discover_codex_models("C:/fake/codex-home")

        self.assertEqual(len(models), 1)
        self.assertIsNone(models[0]["supported"])  # unknown, not "no models"


class CodexModelPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """refresh_codex_models() / list_codex_models() / set_codex_selected_model()
    - client-scoped read-modify-write against the existing connector_oauth_tokens
    row's token_payload JSON column (no schema change)."""

    async def test_refresh_backfills_model_only_when_nothing_was_selected(self):
        from app.codex import refresh_codex_models

        stored_token = {
            "auth_type": "codex_chatgpt",
            "codex_home": "C:/fake/codex-home",
            "model": None,
            "created_at": "2026-09-23T00:00:00+00:00",
        }
        discovered = [
            {"id": "gpt-6-luna", "display_name": "GPT-6 Luna", "description": "",
             "reasoning_support": None, "tool_support": None, "supported": True, "default": True},
        ]
        with patch("app.codex.get_connector_token", new=AsyncMock(return_value=stored_token)), \
             patch("app.codex.upsert_connector_token", new=AsyncMock()) as upsert, \
             patch("app.codex.Path.is_dir", return_value=True), \
             patch("app.codex.discover_codex_models", return_value=discovered):
            models = await refresh_codex_models("client-1")

        self.assertEqual(models, discovered)
        written = upsert.call_args.args[2]
        self.assertEqual(written["model"], "gpt-6-luna")
        self.assertEqual(written["codex_home"], "C:/fake/codex-home")  # preserved, not wiped
        self.assertEqual(written["auth_type"], "codex_chatgpt")  # preserved, not wiped
        self.assertEqual(written["available_models"], discovered)

    async def test_refresh_never_overrides_an_explicit_existing_selection(self):
        from app.codex import refresh_codex_models

        stored_token = {
            "auth_type": "codex_chatgpt",
            "codex_home": "C:/fake/codex-home",
            "model": "gpt-5.5",  # user already picked this explicitly
        }
        discovered = [
            {"id": "gpt-6-luna", "display_name": "GPT-6 Luna", "description": "",
             "reasoning_support": None, "tool_support": None, "supported": True, "default": True},
            {"id": "gpt-5.5", "display_name": "GPT-5.5", "description": "",
             "reasoning_support": True, "tool_support": False, "supported": True, "default": False},
        ]
        with patch("app.codex.get_connector_token", new=AsyncMock(return_value=stored_token)), \
             patch("app.codex.upsert_connector_token", new=AsyncMock()) as upsert, \
             patch("app.codex.Path.is_dir", return_value=True), \
             patch("app.codex.discover_codex_models", return_value=discovered):
            await refresh_codex_models("client-1")

        written = upsert.call_args.args[2]
        self.assertEqual(written["model"], "gpt-5.5")  # untouched by the fresh default

    async def test_refresh_self_heals_a_stored_model_that_is_no_longer_supported(self):
        # This is the real, live-confirmed bug behind the screenshot showing
        # "gpt-4.1-mini" for an already-connected client: that value was
        # written by the OLD frontend placeholder before this discovery
        # system existed, was never a real Codex selection (confirmed live -
        # Codex's ChatGPT auth mode rejects it outright), and nothing had
        # ever corrected the stored row since discovery had never run
        # against it. A stored model that fresh discovery does NOT confirm
        # as supported must self-heal to the real verified default, not
        # persist indefinitely.
        from app.codex import refresh_codex_models

        stored_token = {
            "auth_type": "codex_chatgpt",
            "codex_home": "C:/fake/codex-home",
            "model": "gpt-4.1-mini",  # the old, never-valid placeholder
        }
        discovered = [
            {"id": "gpt-6-luna", "display_name": "GPT-6 Luna", "description": "",
             "reasoning_support": None, "tool_support": None, "supported": True, "default": True},
            {"id": "gpt-5.5", "display_name": "GPT-5.5", "description": "",
             "reasoning_support": True, "tool_support": False, "supported": True, "default": False},
        ]
        with patch("app.codex.get_connector_token", new=AsyncMock(return_value=stored_token)), \
             patch("app.codex.upsert_connector_token", new=AsyncMock()) as upsert, \
             patch("app.codex.Path.is_dir", return_value=True), \
             patch("app.codex.discover_codex_models", return_value=discovered):
            await refresh_codex_models("client-1")

        written = upsert.call_args.args[2]
        self.assertEqual(written["model"], "gpt-6-luna")  # corrected to the real default
        self.assertEqual(written["codex_home"], "C:/fake/codex-home")  # preserved

    async def test_list_codex_models_uses_fresh_cache_without_rediscovering(self):
        from app.codex import list_codex_models
        from app.core import utc_now

        cached = [{"id": "gpt-6-luna", "supported": True, "default": True}]
        stored_token = {
            "auth_type": "codex_chatgpt",
            "available_models": cached,
            "models_checked_at": utc_now().isoformat(),
        }
        with patch("app.codex.get_connector_token", new=AsyncMock(return_value=stored_token)), \
             patch("app.codex.refresh_codex_models", new=AsyncMock()) as refresh:
            models = await list_codex_models("client-1")

        self.assertEqual(models, cached)
        refresh.assert_not_called()

    async def test_list_codex_models_serves_stale_cache_immediately_and_revalidates_in_background(self):
        # Contract change (deliberate): a stale-but-verified cache used to make the
        # request BLOCK on a full multi-minute discovery round (measured live:
        # ~128s). It is now returned immediately and re-verified in the background.
        from datetime import timedelta

        from app.codex import list_codex_models
        from app.core import utc_now

        stale_models = [{"id": "old-model", "supported": True, "default": True}]
        stale_token = {
            "auth_type": "codex_chatgpt",
            "available_models": stale_models,
            "models_checked_at": (utc_now() - timedelta(hours=48)).isoformat(),
        }
        with patch("app.codex.get_connector_token", new=AsyncMock(return_value=stale_token)),              patch("app.codex._start_codex_refresh") as start_refresh:
            models = await list_codex_models("client-1")

        self.assertEqual(models, stale_models)
        start_refresh.assert_called_once_with("client-1")

    async def test_set_selected_model_rejects_unverified_model_id(self):
        from app.codex import set_codex_selected_model

        stored_token = {
            "auth_type": "codex_chatgpt",
            "available_models": [{"id": "gpt-6-luna", "supported": True}, {"id": "gpt-6-astra", "supported": False}],
        }
        with patch("app.codex.get_connector_token", new=AsyncMock(return_value=stored_token)), \
             patch("app.codex.upsert_connector_token", new=AsyncMock()) as upsert:
            with self.assertRaises(ValueError):
                await set_codex_selected_model("client-1", "gpt-6-astra")  # verified-unsupported
            with self.assertRaises(ValueError):
                await set_codex_selected_model("client-1", "gpt-9-made-up")  # never verified at all
        upsert.assert_not_called()

    async def test_set_selected_model_persists_a_verified_choice(self):
        from app.codex import set_codex_selected_model

        stored_token = {
            "auth_type": "codex_chatgpt",
            "codex_home": "C:/fake/codex-home",
            "available_models": [{"id": "gpt-6-luna", "supported": True}, {"id": "gpt-5.5", "supported": True}],
        }
        with patch("app.codex.get_connector_token", new=AsyncMock(return_value=stored_token)), \
             patch("app.codex.upsert_connector_token", new=AsyncMock()) as upsert:
            result = await set_codex_selected_model("client-1", "gpt-5.5")

        self.assertEqual(result, {"model": "gpt-5.5"})
        written = upsert.call_args.args[2]
        self.assertEqual(written["model"], "gpt-5.5")
        self.assertEqual(written["codex_home"], "C:/fake/codex-home")  # preserved

    async def test_client_isolation_two_clients_never_cross_contaminate_selection(self):
        # Phase 8/12: one client's selection must never leak into another's -
        # each call is keyed strictly by the client_id argument, never a
        # module-level/global variable.
        from app.codex import set_codex_selected_model

        tokens_by_client = {
            "client-a": {
                "auth_type": "codex_chatgpt", "codex_home": "C:/a",
                "available_models": [{"id": "gpt-6-luna", "supported": True}],
            },
            "client-b": {
                "auth_type": "codex_chatgpt", "codex_home": "C:/b",
                "available_models": [{"id": "gpt-5.5", "supported": True}],
            },
        }
        written_per_client: dict[str, dict] = {}

        async def fake_get(client_id, _source_type):
            return tokens_by_client[client_id]

        async def fake_upsert(client_id, _source_type, token, user_id=None):
            written_per_client[client_id] = token

        with patch("app.codex.get_connector_token", new=fake_get), \
             patch("app.codex.upsert_connector_token", new=fake_upsert):
            await set_codex_selected_model("client-a", "gpt-6-luna")
            await set_codex_selected_model("client-b", "gpt-5.5")

        self.assertEqual(written_per_client["client-a"]["model"], "gpt-6-luna")
        self.assertEqual(written_per_client["client-b"]["model"], "gpt-5.5")
        self.assertEqual(written_per_client["client-a"]["codex_home"], "C:/a")
        self.assertEqual(written_per_client["client-b"]["codex_home"], "C:/b")


class DemoClientContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_demo_context_uses_demo_sources(self):
        request = ChatRequest(
            question="test",
            client_id="demo-acme",
            client_name="Acme",
            connected_sources=["google_ads"],
        )
        user = AuthUser(sub="test-user", access_token="test-token")

        context = await load_client_context(request, user)

        self.assertEqual(context["id"], "demo-acme")
        self.assertEqual(context["connected_sources"], ["google_ads"])


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_supabase_user_endpoint_validates_non_hs256_token(self):
        response = SimpleNamespace(
            is_success=True,
            json=lambda: {
                "id": "user-1",
                "email": "user@example.com",
                "role": "authenticated",
            },
        )
        environment = {
            "SUPABASE_URL": "https://project.supabase.co",
            "VITE_SUPABASE_ANON_KEY": "anon-key",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("app.auth.httpx.AsyncClient") as async_client,
        ):
            async_client.return_value.__aenter__.return_value.get.return_value = response
            user = await verify_bearer_token("Bearer asymmetric-token")

        self.assertEqual(user.sub, "user-1")
        self.assertEqual(user.access_token, "asymmetric-token")


class ChatStreamingTests(unittest.TestCase):
    def test_stream_chat_response_forwards_mcp_tools_and_emits_sse(self):
        request = ChatRequest(
            question="Which campaigns performed best?",
            client_id="demo-acme",
            client_name="Acme",
            connected_sources=["google_ads"],
        )
        client_context = {
            "id": "demo-acme",
            "name": "Acme",
            "connected_sources": ["google_ads"],
        }
        tools = [{"type": "mcp", "server_label": "google_ads"}]
        events = [
            SimpleNamespace(type="response.output_text.delta", delta="Answer"),
            SimpleNamespace(type="response.completed"),
        ]

        with patch("app.chat_service.OpenAI") as openai:
            openai.return_value.responses.create.return_value = events
            output = "".join(stream_chat_response(request, client_context, tools))

        call = openai.return_value.responses.create.call_args.kwargs
        self.assertEqual(call["tools"], tools)
        self.assertTrue(call["stream"])
        self.assertIn("event: message", output)
        self.assertIn('"delta": "Answer"', output)
        self.assertIn("event: done", output)

    def test_system_prompt_carries_explicit_grounding_rules(self):
        # Regression guard for the anti-hallucination rules added to the
        # shared system prompt: never invent a value, always state a real
        # zero rather than "no data", never say "I will check" once the
        # tool result is already in hand, and keep multi-platform figures
        # attributed to their own source. These are restated to the model
        # even though several are also enforced in code (see
        # detected_tool_error_message / is_non_answer_provider_text) so the
        # model never contradicts that behavior on a payload shape those
        # checks don't recognize.
        request = ChatRequest(
            question="How much did we spend?",
            client_id="demo-acme",
            client_name="Acme",
            connected_sources=["google_ads"],
        )
        client_context = {"id": "demo-acme", "name": "Acme", "connected_sources": ["google_ads"]}
        tools = [{"type": "mcp", "server_label": "google_ads"}]
        events = [SimpleNamespace(type="response.completed")]

        with patch("app.chat_service.OpenAI") as openai:
            openai.return_value.responses.create.return_value = events
            "".join(stream_chat_response(request, client_context, tools))

        instructions = openai.return_value.responses.create.call_args.kwargs["instructions"]
        self.assertIn("never invent or estimate a number", instructions.lower())
        self.assertIn("real, reportable value", instructions.lower())
        self.assertIn("never say you will check", instructions.lower())
        self.assertIn("keep each value attributed to the platform", instructions.lower())

    def test_klaviyo_account_question_maps_to_direct_mcp_intent(self):
        request = ChatRequest(
            question="Show me my Klaviyo account details.",
            client_id="client-1",
            client_name="Test",
            connected_sources=["klaviyo"],
        )
        intent = klaviyo_direct_intent_for_question(request, ["klaviyo"])

        self.assertEqual(intent, "account_details")

    def test_account_intent_accepts_namespaced_klaviyo_tool_name(self):
        tool_name = select_klaviyo_tool_name(
            "account_details",
            [{"name": "klaviyo_get_account_details"}],
        )

        self.assertEqual(tool_name, "klaviyo_get_account_details")

    def test_klaviyo_tool_arguments_include_model_for_any_real_tool_name(self):
        # Confirmed live on 2026-09-23: none of the real Klaviyo MCP
        # server's 122 tools are named with a "klaviyo_" prefix (that
        # prefix never appears anywhere in the real tool list), so a check
        # gated on that prefix never added "model" to any real call, and
        # every Klaviyo tool call failed with "model: Missing required
        # keyword only argument" - exactly as seen live in Ask AI.
        arguments = build_klaviyo_tool_arguments("get_account_details")

        self.assertEqual(arguments["model"], "braidscope-ai")

    def test_direct_klaviyo_result_is_summarized_without_remote_mcp_tools(self):
        request = ChatRequest(
            question="Show me my Klaviyo account details.",
            client_id="client-1",
            client_name="Test",
            connected_sources=["klaviyo"],
        )
        client_context = {
            "id": "client-1",
            "name": "Test",
            "connected_sources": ["klaviyo"],
        }
        tools = [{"type": "mcp", "server_label": "klaviyo"}]
        direct_result = {
            "server_label": "klaviyo",
            "intent": "account_details",
            "result": {"result": {"account": {"name": "Demo Account"}}},
        }
        events = [SimpleNamespace(type="response.completed")]

        with patch("app.chat_service.OpenAI") as openai:
            openai.return_value.responses.create.return_value = events
            output = "".join(
                stream_chat_response(request, client_context, tools, direct_result)
            )

        call = openai.return_value.responses.create.call_args.kwargs
        self.assertEqual(call["tools"], [])
        self.assertIn("normalized MCP result", call["input"])
        self.assertNotIn("MCP tool result JSON", call["input"])
        self.assertNotIn("tool_choice", call)
        self.assertIn("event: done", output)

    def test_completed_response_text_is_forwarded_when_no_delta_events_exist(self):
        request = ChatRequest(
            question="List HubSpot contacts.",
            client_id="client-1",
            client_name="Test",
            connected_sources=["hubspot_crm"],
        )
        client_context = {
            "id": "client-1",
            "name": "Test",
            "connected_sources": ["hubspot_crm"],
        }
        response = SimpleNamespace(
            output_text="Contact: Example Person",
            output=[],
            error=None,
        )
        events = [SimpleNamespace(type="response.completed", response=response)]

        with patch("app.chat_service.OpenAI") as openai:
            openai.return_value.responses.create.return_value = events
            output = "".join(stream_chat_response(request, client_context, []))

        self.assertIn("Contact: Example Person", output)
        self.assertIn("event: done", output)

    def test_hubspot_contact_question_maps_to_direct_mcp_intent(self):
        request = ChatRequest(
            question="Show me my HubSpot contacts.",
            client_id="client-1",
            client_name="Test",
            connected_sources=["hubspot_crm"],
        )
        intent = hubspot_direct_intent_for_question(request, ["hubspot_crm"])

        self.assertEqual(intent, "list_contacts")

    def test_orchestrator_fallback_platform_uses_query_hints(self):
        platform = fallback_platform(
            "List HubSpot deals with stage and amount.",
            [PLATFORMS["klaviyo"], PLATFORMS["hubspot_crm"]],
        )

        self.assertEqual(platform.label, "hubspot_crm")

    def test_klaviyo_orders_question_resolves_with_multiple_platforms_connected(self):
        # Captured live on 2026-09-23: with Google Ads, GA4, and Klaviyo all
        # connected, "How many orders were placed in the last 30 days?" - an
        # ordinary question about Klaviyo's own most common metric ("Placed
        # Order") - matched none of Klaviyo's hints (no "order"/"revenue"/
        # "purchase" terms existed), so it resolved to no platform at all
        # ("No relevant read-only MCP tool matched"), while the unambiguous
        # Klaviyo/Ads/GA4 questions below must keep working exactly as before.
        platforms = [PLATFORMS["google_ads"], PLATFORMS["google_analytics_4"], PLATFORMS["klaviyo"]]
        self.assertEqual(
            fallback_platform("How many orders were placed in the last 30 days?", platforms).label,
            "klaviyo",
        )
        self.assertEqual(
            fallback_platform("What are my Klaviyo account details?", platforms).label,
            "klaviyo",
        )
        self.assertEqual(
            fallback_platform("How much did we spend on ads this month?", platforms).label,
            "google_ads",
        )
        self.assertEqual(
            fallback_platform("How many sessions did we get this week?", platforms).label,
            "google_analytics_4",
        )

    def test_single_platform_question_with_no_hint_overlap_still_asks_ai(self):
        # Captured live on 2026-09-23: with only GA4 connected, "Draft the
        # monthly summary" shares no words with GA4's hints/aliases or the
        # generic data_terms set, so fallback_platform() returned None and
        # the old code short-circuited straight back to None without ever
        # asking the AI planner - the user got "No relevant read-only MCP
        # tool matched" for an obviously GA4-relevant question. (A related
        # real example, "Show me our top 10 most viewed pages this month",
        # is covered by GA4PlatformHintPhraseTests instead now that "pages"/
        # "view" are GA4 hints - it correctly resolves via the faster
        # keyword fast-path below instead of needing this AI-planner path,
        # so it's no longer a "zero overlap" example.)
        with patch(
            "app.mcp_orchestrator.complete_planner_json",
            return_value={"platform": "google_analytics_4"},
        ) as planner:
            platform = choose_platform_with_ai(
                "Draft the monthly summary",
                [PLATFORMS["google_analytics_4"]],
            )
        self.assertEqual(platform.label, "google_analytics_4")
        planner.assert_called_once()

    def test_single_platform_fast_path_skips_ai_when_hint_matches(self):
        with patch("app.mcp_orchestrator.complete_planner_json") as planner:
            platform = choose_platform_with_ai(
                "How many sessions did we have?",
                [PLATFORMS["google_analytics_4"]],
            )
        self.assertEqual(platform.label, "google_analytics_4")
        planner.assert_not_called()

    def test_orchestrator_blocks_destructive_tools(self):
        self.assertFalse(tool_is_read_only({"name": "delete_contact"}))
        self.assertFalse(
            tool_is_read_only(
                {
                    "name": "crm_objects",
                    "description": "Create or update CRM objects",
                    "inputSchema": {
                        "properties": {
                            "createRequest": {"type": "object"},
                            "updateRequest": {"type": "object"},
                        }
                    },
                }
            )
        )
        self.assertTrue(tool_is_read_only({"name": "search_crm_objects"}))
        self.assertTrue(
            tool_is_read_only(
                {
                    "name": "klaviyo_get_flows",
                    "description": "Get flows. Does not create, update, or delete data.",
                }
            )
        )
        self.assertTrue(tool_is_read_only({"name": "klaviyo_get_metrics"}))

    def test_orchestrator_tool_selector_can_fallback_to_read_only_tool(self):
        tools = [
            {"name": "delete_contact", "description": "Delete a CRM contact"},
            {"name": "search_crm_objects", "description": "Search CRM contacts, companies, and deals"},
        ]

        with patch("app.mcp_orchestrator.OpenAI") as openai:
            openai.return_value.responses.create.side_effect = ValueError("planner unavailable")
            tool_call = choose_tool_with_ai(
                "Show me HubSpot contacts.",
                PLATFORMS["hubspot_crm"],
                tools,
            )

        self.assertEqual(tool_call["tool_name"], "search_crm_objects")

    def test_orchestrator_forces_hubspot_tickets_to_search_tool(self):
        tools = [
            {
                "name": "crm_objects",
                "description": "Create or update CRM objects",
                "inputSchema": {
                    "properties": {
                        "createRequest": {"type": "object"},
                        "updateRequest": {"type": "object"},
                    }
                },
            },
            {
                "name": "search_crm_objects",
                "description": "Search CRM objects including contacts, companies, deals, and tickets",
            },
        ]

        with patch("app.mcp_orchestrator.OpenAI") as openai:
            openai.return_value.responses.create.return_value.output_text = (
                '{"tool_name":"crm_objects","arguments":{}}'
            )
            tool_call = choose_tool_with_ai(
                "Show HubSpot tickets with subject, status, and created date.",
                PLATFORMS["hubspot_crm"],
                tools,
            )

        self.assertEqual(tool_call["tool_name"], "search_crm_objects")

    def test_orchestrator_repairs_hubspot_company_arguments(self):
        arguments = normalize_arguments(
            PLATFORMS["hubspot_crm"],
            "Show me my HubSpot companies.",
            "search_crm_objects",
            {},
        )

        self.assertEqual(arguments["objectType"], "companies")
        self.assertIn("name", arguments["properties"])
        self.assertEqual(arguments["limit"], 10)

    def test_orchestrator_repairs_hubspot_deal_arguments(self):
        arguments = normalize_arguments(
            PLATFORMS["hubspot_crm"],
            "List HubSpot deals with deal name, stage, amount, and close date.",
            "search_crm_objects",
            {},
        )

        self.assertEqual(arguments["objectType"], "deals")
        self.assertIn("dealname", arguments["properties"])
        self.assertIn("closedate", arguments["properties"])

    def test_orchestrator_repairs_hubspot_ticket_arguments(self):
        arguments = normalize_arguments(
            PLATFORMS["hubspot_crm"],
            "Show HubSpot tickets with subject, status, and created date.",
            "search_crm_objects",
            {},
        )

        self.assertEqual(arguments["objectType"], "tickets")
        self.assertIn("subject", arguments["properties"])
        self.assertIn("hs_pipeline_stage", arguments["properties"])

    def test_orchestrator_never_injects_fields_the_real_klaviyo_schema_lacks(self):
        # Confirmed live on 2026-09-23 against the real Klaviyo MCP server:
        # get_campaigns' actual schema has no "channel" property at all
        # (filtering there is done via a required "filter" string instead),
        # and blindly injecting one used to risk breaking the call outright
        # given Klaviyo's schemas reject unknown properties. Whatever
        # arguments the caller (the AI planner, which sees the real schema)
        # already built must pass through unchanged, with only "model" added.
        arguments = normalize_arguments(
            PLATFORMS["klaviyo"],
            "List my recent Klaviyo campaigns with their status.",
            "get_campaigns",
            {"filter": "equals(messages.channel,'email')"},
        )

        self.assertNotIn("channel", arguments)
        self.assertNotIn("limit", arguments)
        self.assertEqual(arguments["filter"], "equals(messages.channel,'email')")
        self.assertEqual(arguments["model"], "braidscope-ai")

    def test_orchestrator_semantic_selector_prefers_hubspot_search_for_deals(self):
        tool = select_best_semantic_tool(
            "List HubSpot deals with deal name, stage, amount, and close date.",
            [
                {"name": "search_crm_objects", "description": "Search CRM objects"},
                {"name": "get_record_details", "description": "Get CRM record details"},
            ],
        )

        self.assertEqual(tool["name"], "search_crm_objects")

    def test_orchestrator_semantic_selector_avoids_account_for_flows(self):
        tool = select_best_semantic_tool(
            "Show me recent Klaviyo flows and whether they are active.",
            [
                {"name": "klaviyo_get_account_details", "description": "Get account details"},
                {"name": "klaviyo_get_flows", "description": "Get flows"},
            ],
        )

        self.assertEqual(tool["name"], "klaviyo_get_flows")

    def test_orchestrator_rejects_ai_selected_account_tool_for_flows(self):
        self.assertFalse(
            tool_matches_question(
                "Show me recent Klaviyo flows and whether they are active.",
                {"name": "klaviyo_get_account_details", "description": "Get account details"},
            )
        )
        self.assertTrue(
            tool_matches_question(
                "Show me recent Klaviyo flows and whether they are active.",
                {"name": "klaviyo_get_flows", "description": "Get flows"},
            )
        )

    def test_orchestrator_avoids_required_report_tools_without_ids(self):
        tool = select_best_semantic_tool(
            "Show Klaviyo email performance or metrics for the last 30 days.",
            [
                {
                    "name": "klaviyo_get_flow_report",
                    "description": "Get flow report",
                    "inputSchema": {"required": ["statistics", "conversionMetricId"]},
                },
                {"name": "klaviyo_get_metrics", "description": "Get metrics"},
                {"name": "klaviyo_get_campaigns", "description": "Get campaigns"},
            ],
        )

        self.assertEqual(tool["name"], "klaviyo_get_metrics")

    def test_orchestrator_detects_unusable_required_schema_args(self):
        self.assertFalse(
            selected_tool_is_usable(
                {"inputSchema": {"required": ["conversionMetricId"]}},
                {},
            )
        )

    def test_orchestrator_resolves_metric_name_from_question(self):
        metric = find_metric_for_question(
            "Show last 30 days Klaviyo performance for Opened Email metric",
            [
                {"type": "metric", "id": "SECyXd", "attributes": {"name": "Placed Order"}},
                {"type": "metric", "id": "RYnc75", "attributes": {"name": "Opened Email"}},
            ],
        )

        self.assertEqual(metric["id"], "RYnc75")

    def test_normalize_arguments_never_reshapes_metric_aggregate_body(self):
        # normalize_klaviyo_arguments() used to force flat top-level
        # "metricId"/"measurements"/"startDate"/"endDate" fields onto every
        # query_metric_aggregates call. Confirmed live on 2026-09-23: that
        # tool's real schema requires a fully nested `body.data.attributes`
        # object with `additionalProperties: false` at every level, so those
        # flat fields were rejected outright as unknown properties. The
        # correct nested shape is built once, by
        # build_klaviyo_metric_aggregate_arguments(), and must pass through
        # normalize_arguments() completely unchanged (plus "model").
        pre_built = build_klaviyo_metric_aggregate_arguments(
            "RYnc75", "2023-08-01T00:00:00Z", "2023-10-31T23:59:59Z",
        )
        arguments = normalize_arguments(
            PLATFORMS["klaviyo"],
            "Show Klaviyo performance for Opened Email metric from 2023-08-01 to 2023-10-31",
            "query_metric_aggregates",
            pre_built,
        )
        self.assertEqual(arguments, pre_built)
        self.assertNotIn("metricId", arguments)
        self.assertNotIn("startDate", arguments)
        self.assertNotIn("limit", arguments)

    def test_build_klaviyo_metric_aggregate_arguments_matches_real_schema(self):
        # Reproduces the real query_metric_aggregates inputSchema fetched
        # live on 2026-09-23: top-level required ["body", "model"];
        # body.data.required ["attributes"];
        # body.data.attributes.required ["metric_id", "measurements", "filter"].
        arguments = build_klaviyo_metric_aggregate_arguments(
            "RYnc75", "2023-08-01T00:00:00Z", "2023-10-31T23:59:59Z",
        )
        self.assertEqual(arguments["model"], "braidscope-ai")
        attributes = arguments["body"]["data"]["attributes"]
        self.assertEqual(arguments["body"]["data"]["type"], "metric-aggregate")
        self.assertEqual(attributes["metric_id"], "RYnc75")
        self.assertEqual(attributes["measurements"], ["count"])
        self.assertEqual(
            attributes["filter"],
            [
                "greater-or-equal(datetime,2023-08-01T00:00:00)",
                "less-than(datetime,2023-10-31T23:59:59)",
            ],
        )
        # The "Z" (UTC) suffix from iso_start_datetime_days_ago/
        # iso_end_datetime_today is stripped - Klaviyo's own filter examples
        # use bare "YYYY-MM-DDTHH:MM:SS" with no zone suffix.
        self.assertNotIn("Z", attributes["filter"][0])

    def test_date_range_parser_swaps_reversed_dates(self):
        date_range = extract_date_range_from_question(
            "Show metric from 2023-10-31 to 2023-08-01"
        )

        self.assertEqual(
            date_range,
            ("2023-08-01T00:00:00Z", "2023-10-31T23:59:59Z"),
        )

    def test_direct_mcp_result_gets_fallback_text_when_formatter_is_blank(self):
        request = ChatRequest(
            question="Show me recent Klaviyo flows.",
            client_id="client-1",
            client_name="Test",
            connected_sources=["klaviyo"],
        )
        client_context = {
            "id": "client-1",
            "name": "Test",
            "connected_sources": ["klaviyo"],
        }
        direct_result = {
            "server_label": "klaviyo",
            "selected_tool": "klaviyo_get_flows",
            "result": {
                "result": {
                    "content": [
                        {"type": "text", "text": "Flow A — active\nFlow B — inactive"}
                    ]
                }
            },
        }
        events = [SimpleNamespace(type="response.completed")]

        with patch("app.chat_service.OpenAI") as openai:
            openai.return_value.responses.create.return_value = events
            output = "".join(
                stream_chat_response(request, client_context, [], direct_result)
            )

        self.assertIn("Flow A", output)
        self.assertIn("event: done", output)

    def test_direct_mcp_result_formats_klaviyo_json_text(self):
        request = ChatRequest(
            question="Show me recent Klaviyo flows.",
            client_id="client-1",
            client_name="Test",
            connected_sources=["klaviyo"],
        )
        client_context = {
            "id": "client-1",
            "name": "Test",
            "connected_sources": ["klaviyo"],
        }
        direct_result = {
            "server_label": "klaviyo",
            "selected_tool": "klaviyo_get_flows",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"data":[{"type":"flow","id":"flow-1",'
                                '"attributes":{"name":"Welcome Flow","status":"live",'
                                '"updated":"2026-06-05T00:00:00Z","triggerType":"Metric"}}]}'
                            ),
                        }
                    ]
                }
            },
        }
        events = [SimpleNamespace(type="response.completed")]

        with patch("app.chat_service.OpenAI") as openai:
            openai.return_value.responses.create.return_value = events
            output = "".join(
                stream_chat_response(request, client_context, [], direct_result)
            )

        self.assertIn("Here are the flows returned", output)
        self.assertIn("Welcome Flow", output)
        self.assertNotIn('{"data"', output)

    def test_direct_mcp_result_includes_structured_aggregate_with_note(self):
        request = ChatRequest(
            question="Show last 30 days Klaviyo performance for Opened Email metric",
            client_id="client-1",
            client_name="Test",
            connected_sources=["klaviyo"],
        )
        client_context = {
            "id": "client-1",
            "name": "Test",
            "connected_sources": ["klaviyo"],
        }
        direct_result = {
            "server_label": "klaviyo",
            "selected_tool": "klaviyo_query_metric_aggregates",
            "result": {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "_data_source_note: Event-time based data.",
                        }
                    ],
                    "data": [
                        {"date": "2026-06-05", "measurements": {"count": 42}},
                    ],
                }
            },
        }
        events = [SimpleNamespace(type="response.completed")]

        with patch("app.chat_service.OpenAI") as openai:
            openai.return_value.responses.create.return_value = events
            output = "".join(
                stream_chat_response(request, client_context, [], direct_result)
            )

        self.assertIn("_data_source_note", output)
        self.assertIn("count: 42", output)

    def test_direct_mcp_result_ignores_jsonrpc_wrapper_for_empty_aggregate(self):
        request = ChatRequest(
            question="Show last 30 days Klaviyo performance for Opened Email metric",
            client_id="client-1",
            client_name="Test",
            connected_sources=["klaviyo"],
        )
        client_context = {
            "id": "client-1",
            "name": "Test",
            "connected_sources": ["klaviyo"],
        }
        direct_result = {
            "server_label": "klaviyo",
            "selected_tool": "klaviyo_query_metric_aggregates",
            "selected_arguments": {
                "metricId": "RYnc75",
                "measurements": ["count"],
                "startDate": "2026-05-06T00:00:00Z",
                "endDate": "2026-06-05T23:59:59Z",
            },
            "resolved_metric": {"id": "RYnc75", "name": "Opened Email"},
            "result": {
                "jsonrpc": "2.0",
                "id": "tool-call",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "_data_source_note: This data is based on EVENT TIME "
                                "(when events occurred), NOT send date."
                            ),
                        }
                    ]
                },
            },
        }
        events = [SimpleNamespace(type="response.completed")]

        with patch("app.chat_service.OpenAI") as openai:
            openai.return_value.responses.create.return_value = events
            output = "".join(
                stream_chat_response(request, client_context, [], direct_result)
            )

        self.assertIn("Klaviyo returned no aggregate metric rows", output)
        self.assertIn("Metric: Opened Email (RYnc75)", output)
        self.assertNotIn("jsonrpc", output)
        self.assertNotIn("tool-call", output)

    def test_direct_mcp_result_reads_nested_jsonrpc_aggregate_rows(self):
        request = ChatRequest(
            question="Show last 30 days Klaviyo performance for Opened Email metric",
            client_id="client-1",
            client_name="Test",
            connected_sources=["klaviyo"],
        )
        client_context = {
            "id": "client-1",
            "name": "Test",
            "connected_sources": ["klaviyo"],
        }
        direct_result = {
            "server_label": "klaviyo",
            "selected_tool": "klaviyo_query_metric_aggregates",
            "result": {
                "jsonrpc": "2.0",
                "id": "tool-call",
                "result": {
                    "data": [
                        {"datetime": "2026-06-05T00:00:00Z", "measurements": {"count": 7}},
                    ]
                },
            },
        }
        events = [SimpleNamespace(type="response.completed")]

        with patch("app.chat_service.OpenAI") as openai:
            openai.return_value.responses.create.return_value = events
            output = "".join(
                stream_chat_response(request, client_context, [], direct_result)
            )

        self.assertIn("count: 7", output)
        self.assertNotIn("jsonrpc", output)
        self.assertNotIn("tool-call", output)


def _fake_google_ads_error(status_code: int, error_code: str, message: str) -> httpx.HTTPStatusError:
    body = json.dumps([{
        "error": {
            "code": status_code,
            "message": "The caller does not have permission",
            "status": "PERMISSION_DENIED" if status_code == 403 else "INVALID_ARGUMENT",
            "details": [{"errors": [{"errorCode": {"authorizationError": error_code}, "message": message}]}],
        }
    }])
    request = httpx.Request("POST", "https://googleads.googleapis.com/fake")
    response = httpx.Response(status_code, text=body, request=request)
    return httpx.HTTPStatusError(message, request=request, response=response)


def _identity_row(cid: str, name: str, manager: bool = False, status: str = "ENABLED") -> dict:
    return {
        "customer": {
            "id": cid, "descriptiveName": name, "manager": manager, "status": status,
        }
    }


class GoogleAdsCustomerResolutionTests(unittest.IsolatedAsyncioTestCase):
    """Regression tests for the canonical Google Ads customer resolver.
    Fixtures 2-4 reproduce the exact real account shape captured live on
    2026-09-23 (a childless manager plus a CUSTOMER_NOT_ENABLED account)."""

    def setUp(self):
        google_ads_dashboard._resolution_cache.clear()

    async def _resolve(self, customers, identities, children=None, customer_id=None):
        children = children or {}

        async def fake_list_customers(client, token):
            return customers

        async def fake_search(client, token, cid, query, login_cid=None):
            result = identities.get(cid)
            if isinstance(result, Exception):
                raise result
            if result is None:
                raise _fake_google_ads_error(403, "CUSTOMER_NOT_ENABLED", "not accessible")
            return [result]

        async def fake_search_as_manager(client, token, manager_cid, query):
            return children.get(manager_cid, [])

        with patch(
            "app.google_ads_dashboard.get_google_ads_access_token",
            new=AsyncMock(return_value="fake-token"),
        ), patch(
            "app.google_ads_dashboard._list_customers", new=AsyncMock(side_effect=fake_list_customers)
        ), patch(
            "app.google_ads_dashboard._search", new=AsyncMock(side_effect=fake_search)
        ), patch(
            "app.google_ads_dashboard._search_as_manager", new=AsyncMock(side_effect=fake_search_as_manager)
        ):
            return await resolve_google_ads_customer("client-1", customer_id=customer_id, force_refresh=True)

    async def test_enabled_normal_customer(self):
        result = await self._resolve(
            customers=["111"],
            identities={"111": _identity_row("111", "Acme Ads")},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["customer_id"], "111")
        self.assertIsNone(result["login_customer_id"])

    async def test_manager_with_no_children(self):
        # Reproduces the real "Braidscope" manager account: ENABLED,
        # accessible, but customer_client returns zero children.
        result = await self._resolve(
            customers=["1549328398"],
            identities={"1549328398": _identity_row("1549328398", "Braidscope", manager=True)},
            children={},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "manager_no_children")
        self.assertIn("Braidscope", result["reason"])
        self.assertIn("manager (MCC) account", result["reason"])

    async def test_manager_with_enabled_child(self):
        result = await self._resolve(
            customers=["999"],
            identities={
                "999": _identity_row("999", "Agency MCC", manager=True),
                "222": _identity_row("222", "Client Account", status="ENABLED"),
            },
            children={"999": [{
                "customerClient": {
                    "id": "222", "descriptiveName": "Client Account",
                    "manager": False, "status": "ENABLED",
                }
            }]},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["customer_id"], "222")
        self.assertEqual(result["login_customer_id"], "999")

    async def test_disabled_customer(self):
        # Reproduces the real "2347663074" account: genuine
        # CUSTOMER_NOT_ENABLED from Google, independent of any header.
        error = _fake_google_ads_error(403, "CUSTOMER_NOT_ENABLED", "not yet enabled or deactivated")
        result = await self._resolve(
            customers=["2347663074"],
            identities={"2347663074": error},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "disabled")
        self.assertIn("not yet enabled or deactivated", result["reason"])

    async def test_first_customer_invalid_second_valid(self):
        error = _fake_google_ads_error(403, "CUSTOMER_NOT_ENABLED", "not accessible")
        result = await self._resolve(
            customers=["bad1", "good1"],
            identities={"bad1": error, "good1": _identity_row("good1", "Good Account")},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["customer_id"], "good1")

    async def test_all_customers_invalid(self):
        error1 = _fake_google_ads_error(403, "CUSTOMER_NOT_ENABLED", "reason one")
        error2 = _fake_google_ads_error(403, "CUSTOMER_NOT_ENABLED", "reason two")
        result = await self._resolve(
            customers=["bad1", "bad2"],
            identities={"bad1": error1, "bad2": error2},
        )
        self.assertFalse(result["ok"])
        self.assertIn("reason one", result["reason"])
        self.assertIn("reason two", result["reason"])

    async def test_this_real_account_reports_both_real_reasons_not_generic_text(self):
        # The exact real shape captured live on 2026-09-23: one childless
        # manager AND one disabled customer, in the same connection. The
        # combined reason must name both real findings, not fall back to
        # "may not have active campaigns or permissions may be restricted."
        error = _fake_google_ads_error(403, "CUSTOMER_NOT_ENABLED", "not yet enabled or has been deactivated")
        result = await self._resolve(
            customers=["2347663074", "1549328398"],
            identities={
                "2347663074": error,
                "1549328398": _identity_row("1549328398", "Braidscope", manager=True),
            },
            children={},
        )
        self.assertFalse(result["ok"])
        self.assertIn("Braidscope", result["reason"])
        self.assertIn("manager (MCC) account", result["reason"])
        self.assertIn("not yet enabled or has been deactivated", result["reason"])
        self.assertNotIn("may not have active campaigns", result["reason"])

    async def test_not_connected(self):
        with patch(
            "app.google_ads_dashboard.get_google_ads_access_token",
            new=AsyncMock(return_value=None),
        ):
            result = await resolve_google_ads_customer("client-1", force_refresh=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "not_connected")

    async def test_client_isolation_separate_cache_and_tokens(self):
        calls: list[str] = []

        async def fake_token(client_id):
            calls.append(client_id)
            return f"token-for-{client_id}"

        async def fake_list_customers(client, token):
            # Each client's token maps to a distinct customer.
            return ["111"] if token == "token-for-client-a" else ["222"]

        async def fake_search(client, token, cid, query, login_cid=None):
            return [_identity_row(cid, f"Account {cid}")]

        with patch(
            "app.google_ads_dashboard.get_google_ads_access_token", new=AsyncMock(side_effect=fake_token)
        ), patch(
            "app.google_ads_dashboard._list_customers", new=AsyncMock(side_effect=fake_list_customers)
        ), patch(
            "app.google_ads_dashboard._search", new=AsyncMock(side_effect=fake_search)
        ):
            result_a = await resolve_google_ads_customer("client-a", force_refresh=True)
            result_b = await resolve_google_ads_customer("client-b", force_refresh=True)

        self.assertEqual(result_a["customer_id"], "111")
        self.assertEqual(result_b["customer_id"], "222")
        self.assertEqual(calls, ["client-a", "client-b"])

    async def test_dashboard_and_mcp_resolve_the_same_customer(self):
        # Both google_ads_dashboard.build_google_ads_dashboard() and
        # mcp_orchestrator.resolve_google_ads_customer_id() must call
        # through to the exact same resolve_google_ads_customer() - proven
        # here by mocking that one function and checking both callers
        # receive its answer, not independently-computed ones.
        with patch(
            "app.mcp_orchestrator.resolve_google_ads_customer",
            new=AsyncMock(return_value={
                "ok": True, "customer_id": "777", "login_customer_id": None,
                "name": "Shared Account", "reason_code": None, "reason": None, "customers": [],
            }),
        ) as mocked:
            resolved = await mcp_resolve_google_ads_customer_id("client-1")
        self.assertEqual(resolved, "777")
        mocked.assert_awaited_once_with("client-1")


class DashboardBuilderCallContractTests(unittest.TestCase):
    """main.py's /py/dashboards/* endpoints always call each platform's
    build_*_dashboard() through get_or_refresh_dashboard_cache's factory
    closure with client_id, days, a platform-specific selector kwarg, and
    force_refresh - regardless of whether that builder's own body uses
    force_refresh. A builder whose signature doesn't accept every keyword
    main.py always passes raises TypeError on every single real request,
    before any connection check or real API call - exactly what
    build_klaviyo_dashboard's own docstring documents happening live, and
    what build_hubspot_dashboard and build_meta_ads_dashboard were also
    doing (confirmed by reading main.py's call sites against their
    signatures) until this test was added alongside their fix. This checks
    the signature generically so the next platform added here fails one
    cheap unit test instead of every production dashboard request."""

    def test_all_dashboard_builders_accept_main_pys_call_signature(self):
        import inspect

        from app.ga4_dashboard import build_ga4_dashboard
        from app.google_ads_dashboard import build_google_ads_dashboard
        from app.hubspot_dashboard import build_hubspot_dashboard
        from app.klaviyo_dashboard import build_klaviyo_dashboard
        from app.meta_ads_dashboard import build_meta_ads_dashboard

        # Each entry mirrors an actual call site in main.py's dashboard
        # endpoints (the get_or_refresh_dashboard_cache factory lambda, or -
        # for GA4 - its force_refresh=True early-return branch too).
        calls = [
            (build_ga4_dashboard, dict(client_id="c", days=30, property_id=None)),
            (build_ga4_dashboard, dict(client_id="c", days=30, property_id=None, force_refresh=True)),
            (build_google_ads_dashboard, dict(client_id="c", days=30, customer_id=None, force_refresh=False)),
            (build_meta_ads_dashboard, dict(client_id="c", days=30, account_id=None, force_refresh=False)),
            (build_hubspot_dashboard, dict(client_id="c", days=30, force_refresh=False)),
            (build_klaviyo_dashboard, dict(client_id="c", days=30, force_refresh=False)),
        ]
        for builder, kwargs in calls:
            signature = inspect.signature(builder)
            try:
                signature.bind(**kwargs)
            except TypeError as exc:
                self.fail(
                    f"{builder.__module__}.{builder.__name__} can't accept the call main.py "
                    f"makes ({kwargs!r}): {exc}"
                )


class ResolveDirectToolResultFallthroughTests(unittest.IsolatedAsyncioTestCase):
    """Confirmed live on 2026-09-24: a transient network error (DNS
    resolution failure) raised out of the FIRST lookup
    (maybe_get_direct_klaviyo_result) used to abort the whole pre-fetch
    chain in the /py/chat endpoint, so the next two lookups - including the
    generic MCP orchestrator, which has its own retry logic and is far more
    resilient to exactly this kind of blip - never got a chance to run.
    "Show me my Klaviyo account details" answered with the generic "No
    relevant read-only MCP tool matched" even though a fallback lookup would
    likely have succeeded. resolve_direct_tool_result must isolate each of
    the three lookups so one's failure never spends the other two's
    chances."""

    def setUp(self):
        self.request = ChatRequest(
            question="Show me my Klaviyo account details.",
            client_id="client-1",
            client_name="Test",
            connected_sources=["klaviyo"],
        )
        self.client_context = {"id": "client-1", "name": "Test", "connected_sources": ["klaviyo"]}
        self.tools = [{"type": "mcp", "server_label": "klaviyo"}]
        self.ai_config = {"provider": "openai_codex", "model": "gpt-5"}

    async def test_first_lookup_network_error_falls_through_to_orchestrator(self):
        orchestrated_result = {"server_label": "klaviyo", "intent": "dynamic_tool_selection", "result": {}}
        with patch(
            "app.main.maybe_get_direct_klaviyo_result",
            new=AsyncMock(side_effect=httpx.ConnectError("getaddrinfo failed")),
        ), patch(
            "app.main.maybe_get_direct_hubspot_result", new=AsyncMock(return_value=None),
        ), patch(
            "app.main.maybe_get_orchestrated_mcp_result", new=AsyncMock(return_value=orchestrated_result),
        ) as mocked_orchestrator:
            result = await resolve_direct_tool_result(self.request, self.client_context, self.tools, self.ai_config)

        self.assertEqual(result, orchestrated_result)
        mocked_orchestrator.assert_awaited_once()

    async def test_first_lookup_network_error_still_allows_second_lookup_to_win(self):
        hubspot_result = {"server_label": "hubspot_crm", "intent": "account_details", "result": {}}
        with patch(
            "app.main.maybe_get_direct_klaviyo_result",
            new=AsyncMock(side_effect=httpx.ConnectError("getaddrinfo failed")),
        ), patch(
            "app.main.maybe_get_direct_hubspot_result", new=AsyncMock(return_value=hubspot_result),
        ), patch(
            "app.main.maybe_get_orchestrated_mcp_result", new=AsyncMock(return_value=None),
        ) as mocked_orchestrator:
            result = await resolve_direct_tool_result(self.request, self.client_context, self.tools, self.ai_config)

        self.assertEqual(result, hubspot_result)
        mocked_orchestrator.assert_not_awaited()

    async def test_all_three_lookups_failing_returns_none_without_raising(self):
        with patch(
            "app.main.maybe_get_direct_klaviyo_result",
            new=AsyncMock(side_effect=httpx.ConnectError("getaddrinfo failed")),
        ), patch(
            "app.main.maybe_get_direct_hubspot_result", new=AsyncMock(return_value=None),
        ), patch(
            "app.main.maybe_get_orchestrated_mcp_result",
            new=AsyncMock(side_effect=httpx.ConnectError("getaddrinfo failed")),
        ):
            result = await resolve_direct_tool_result(self.request, self.client_context, self.tools, self.ai_config)

        self.assertIsNone(result)


class GA4PlatformHintPhraseTests(unittest.TestCase):
    """Confirmed live on 2026-09-24: "What are our top 5 pages by views
    this month?" - an entirely ordinary GA4 question - matched none of
    GA4's platform hints. "top pages" requires that exact substring, so
    inserting a number ("top 5 pages") breaks it, and bare "pages"/"views"
    were never hints at all. With more than one platform connected this
    heuristic is only the fallback behind the AI platform planner, but that
    call can itself fail (rate limit, provider outage, a transient network
    blip) and silently drop back to this same keyword heuristic - which
    must not go silent just because a number was inserted into an
    otherwise obvious phrasing."""

    def test_top_n_pages_by_views_routes_to_ga4_even_with_other_platforms_connected(self):
        platforms = [
            PLATFORMS["google_analytics_4"],
            PLATFORMS["google_ads"],
            PLATFORMS["meta_ads"],
            PLATFORMS["klaviyo"],
        ]
        platform = fallback_platform("What are our top 5 pages by views this month?", platforms)
        self.assertIsNotNone(platform)
        self.assertEqual(platform.label, "google_analytics_4")


if __name__ == "__main__":
    unittest.main()

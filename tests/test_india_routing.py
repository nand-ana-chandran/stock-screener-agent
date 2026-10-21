import os
import unittest
from unittest.mock import patch


with patch.dict(os.environ, {"GOOGLE_API_KEY": "offline-test-placeholder"}):
    from stock_screener.flow import SYSTEM_PROMPT
from stock_screener.tool import (
    company_snapshot,
    custom_screener,
    india_screener,
    simple_screener,
)


class TestIndiaRoutingGuidance(unittest.TestCase):
    def test_prompt_routes_india_and_nse_symbols_only_to_india_screener(self):
        prompt = SYSTEM_PROMPT.casefold()
        self.assertIn("indian company, nse symbol", prompt)
        self.assertIn("use india_screener only", prompt)
        self.assertIn("never send an nse symbol to company_snapshot", prompt)
        self.assertIn("use india_screener instead", prompt)
        self.assertIn("us adr", prompt)
        self.assertIn("infy or hdb", prompt)
        self.assertIn("rupee question", prompt)
        self.assertIn("technology sector", prompt)
        self.assertIn("sector='technology'", prompt)
        self.assertIn("industry='it' only", prompt)
        self.assertIn("tcs, infosys, wipro, and hcltech", prompt)
        self.assertIn("industry='software'", prompt)
        self.assertIn("for 'tech', 'technology', or 'technology sector'", prompt)
        self.assertIn("rows reporting in other currencies are not ranked in this ranking", prompt)
        self.assertIn("currency group and its count", prompt)
        self.assertIn("never merge those groups into one ranking", prompt)
        self.assertIn("latest-quarter yoy revenue growth (yahoo)", prompt)
        self.assertIn("often reflect mergers or accounting changes", prompt)
        self.assertIn("yahoo finance is temporarily refusing requests from this server", prompt)
        self.assertIn("never describe this as an authorization or permissions error", prompt)
        self.assertIn("asking an india/nse question", prompt)
        self.assertIn("cannot compare or filter those two fields directly", prompt)
        self.assertIn("do not substitute a top-n-by-debt list", prompt)
        self.assertIn("no exchange-rate data is available", prompt)
        self.assertIn("give the reported usd figure with its usd label", prompt)
        self.assertIn("never convert it or guess an exchange rate", prompt)
        self.assertIn("companies with '<text>' in the name", prompt)
        self.assertIn("state the resolved sector and/or industry labels used", prompt)
        self.assertIn("consumer defensive for fmcg", prompt)

    def test_tool_descriptions_distinguish_india_from_us_tools(self):
        india_description = india_screener.description.casefold()
        self.assertIn("nse symbols must use this tool", india_description)
        self.assertIn("total_cash", india_description)
        self.assertIn("total_debt", india_description)
        self.assertIn('pass industry="it"', india_description)
        self.assertIn('use sector="technology"', india_description)
        self.assertIn("technology sector requests", india_description)
        self.assertIn("information technology services", india_description)
        self.assertIn("software industries", india_description)
        self.assertIn('pass industry="it"', india_description)
        self.assertIn("tcs, infosys, wipro, and hcltech", india_description)
        self.assertIn('"software" alone covers software industries', india_description)
        self.assertIn("financial_currency", india_description)
        for tool in (company_snapshot, custom_screener, simple_screener):
            description = tool.description.casefold()
            self.assertIn("india_screener", description)
            self.assertIn("nse", description)
            self.assertIn("rupee", description)


if __name__ == "__main__":
    unittest.main(verbosity=2)

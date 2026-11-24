"""MCP-Vertrag mit lokaler Planung und simulierten externen Diensten."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, SKOS
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import mcp_server
import planung


class RoutifyMcpTest(unittest.TestCase):
    def test_route_und_ontologie_ueber_http(self):
        coords = [[8.27, 49.99, 91], [8.22, 50.01, 95], [8.17, 50.02, 96]]
        orte = {"Mainz": coords[0], "Budenheim": coords[-1]}

        async def geocode(client, name, **kwargs):
            if name in orte:
                lon, lat, _ = orte[name]
                return {"lon": lon, "lat": lat, "name": name, "label": name}

        async def brouter(client, points, profile, alt):
            self.assertEqual((profile, alt), ("trekking", 0))
            return {"coords": coords, "props": {}}

        async def wetter(*args):
            return {}

        with (
            patch.dict("os.environ", {"NO_PROXY": "*", "no_proxy": "*"}),
            patch.object(planung, "geocode", geocode),
            patch.object(planung, "brouter", brouter),
            patch("plausibel.KACHELN", {(5, 45), (5, 50)}),
            patch.object(planung, "weather", wetter),
            patch.object(planung, "extract_params", side_effect=AssertionError("Ollama aufgerufen")),
            TestClient(mcp_server.app, base_url="http://localhost:8103") as client,
        ):
            def rpc(method, params=None):
                response = client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                    headers={"Accept": "application/json, text/event-stream"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()["result"]

            init = rpc("initialize", {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            })
            self.assertEqual(init["serverInfo"]["name"], "Routify")

            tool = rpc("tools/list")["tools"][0]
            self.assertEqual(tool["name"], "fahrradroute_berechnen")
            self.assertIn("outputSchema", tool)
            uri = rpc("resources/list")["resources"][0]["uri"]
            ttl = rpc("resources/read", {"uri": uri})["contents"][0]["text"]
            graph = Graph().parse(data=ttl, format="turtle")
            for concept in mcp_server.POI_BEGRIFFE.values():
                self.assertIn((URIRef(concept), RDF.type, SKOS.Concept), graph)

            args = {"start": "Mainz", "ziel": "Budenheim", "poi_kategorien": ["bahnhof", "verleih"], "rdf": True}
            result = rpc("tools/call", {"name": tool["name"], "arguments": args})
            self.assertFalse(result.get("isError"), result)
            data = result["structuredContent"]
            self.assertEqual(data["status"], "ok", data)
            query = parse_qs(urlsplit(data["routify_url"]).query)
            self.assertEqual(query["start"], ["Mainz"])
            self.assertEqual(query["ziel"], ["Budenheim"])
            self.assertEqual(query["poi"], ["bahnhof", "verleih"])
            self.assertEqual(data["route"]["geometrie"]["coordinates"][0], [8.27, 49.99])
            self.assertEqual([p["kategorie"] for p in data["poi"]], ["bahnhof", "verleih"])
            self.assertGreater(len(Graph().parse(data=data["turtle"], format="turtle")), 0)

            args["zwischenziele"] = ["Unbekannt"]
            result = rpc("tools/call", {"name": tool["name"], "arguments": args})
            self.assertEqual(result["structuredContent"]["status"], "rueckfrage")

            args["poi_kategorien"] = ["trinkwasser"]
            result = rpc("tools/call", {"name": tool["name"], "arguments": args})
            self.assertTrue(result["isError"])


if __name__ == "__main__":
    unittest.main()

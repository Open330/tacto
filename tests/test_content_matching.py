"""A request finds a promoted recipe by what it asks, not how it is worded.

Zero tokens, and conservative on purpose: a recipe found wrongly replays with
nobody watching. The case that shaped it: an early version dropped the verb as
"just how it was asked" and matched "9를 3으로 곱해줘" to a DIVIDE recipe.
"""
import unittest

from tacto.agent import Agent
from tacto.flow import Flow, content_match

SQRT = "계산기에서 {{n}}의 제곱근 구해줘"
DIVIDE = "계산기에서 {{a}}을 {{b}}로 나눠줘"


class ContentMatchTests(unittest.TestCase):
    def test_wording_that_does_not_change_the_request_still_matches(self):
        for said in ("계산기로 49 제곱근 좀 알려줄래?", "계산기에서 49의 제곱근을 계산해 주세요",
                     "49 제곱근 계산기", "계산기로 49의 제곱근 구하기", "계산기로 49 제곱근 알려줘요"):
            self.assertEqual({"n": "49"}, content_match(SQRT, {"n": "25"}, said), said)

    def test_the_verb_is_part_of_what_was_asked(self):
        self.assertIsNone(content_match(DIVIDE, {"a": "6", "b": "2"}, "계산기에서 9를 3으로 곱해줘"))
        self.assertIsNone(content_match(SQRT, {"n": "25"}, "계산기에서 49의 제곱근 지워줘"))

    def test_more_words_or_numbers_is_a_different_request(self):
        for said in ("계산기에서 49의 제곱근의 두 배 구해줘", "계산기에서 49와 50의 제곱근 구해줘",
                     "49의 제곱근 구해줘", "계산기에서 마흔아홉의 제곱근 구해줘"):
            self.assertIsNone(content_match(SQRT, {"n": "25"}, said), said)

    def test_two_numbers_are_told_apart_by_their_particles(self):
        p = {"a": "6", "b": "2"}
        self.assertEqual({"a": "9", "b": "3"}, content_match(DIVIDE, p, "계산기에서 9를 3으로 나눠 줘"))
        self.assertEqual({"a": "9", "b": "3"}, content_match(DIVIDE, p, "계산기에서 3으로 9를 나눠줘"))
        self.assertIsNone(content_match(DIVIDE, p, "계산기에서 9 3 나눠줘"))

    def test_only_numeric_inputs_can_be_read_this_way(self):
        self.assertIsNone(content_match("{{who}}에게 메일 보내줘", {"who": "민수"}, "영희에게 메일 보내줘"))


class ResolveTests(unittest.TestCase):
    def agent(self, *flows):
        store = type("S", (), {"list": lambda s: [f.name for f in flows],
                               "load": lambda s, n: next(f for f in flows if f.name == n)})()
        return Agent(driver=object(), store=store)

    def test_a_reworded_request_finds_its_recipe_without_a_model(self):
        agent = self.agent(Flow("sqrt", "t", params={"n": "25"}, intents=[SQRT]))
        m = agent.resolve("계산기로 225 제곱근 좀 알려줘요")
        self.assertEqual(("sqrt", {"n": "225"}), (m.name, m.params))

    def test_negation_goes_to_the_reading_instead(self):
        agent = self.agent(Flow("sqrt", "t", params={"n": "25"}, intents=[SQRT]))
        self.assertIsNone(agent.resolve("계산기에서 5 세제곱 말고 제곱근 구해줘"))

    def test_two_recipes_claiming_it_means_neither(self):
        agent = self.agent(Flow("a", "t", params={"n": "1"}, intents=[SQRT]),
                           Flow("b", "t", params={"n": "1"}, intents=["{{n}}의 제곱근 계산기에서 구해줘"]))
        self.assertIsNone(agent.resolve("계산기로 49 제곱근 좀 알려줘"))


if __name__ == "__main__":
    unittest.main()

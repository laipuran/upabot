import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.support import FakeEvent

# support isolates runtime data before SDK imports.
# isort: split
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.response import AgentStats
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import ToolSet
from astrbot.core.provider.entities import LLMResponse, ProviderRequest


class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def assert_text_only_request(self, mode, tools, streaming=False):
        runner = ToolLoopAgentRunner()
        context = ContextWrapper(context=SimpleNamespace(event=FakeEvent("测试")))
        await runner.reset(
            provider=Mock(provider_config={"id": "test"}),
            request=ProviderRequest(
                prompt="测试",
                system_prompt="测试人格",
                contexts=[
                    {"role": "user", "content": "历史问题"},
                    {"role": "assistant", "content": "历史回复"},
                ],
                func_tool=tools,
            ),
            run_context=context,
            tool_executor=Mock(),
            agent_hooks=BaseAgentRunHooks(),
            streaming=streaming,
            tool_schema_mode=mode,
        )
        # Check messages as well as stats: just moving stats above the early
        # return would hide the exception while dropping the user's input.
        self.assertEqual(
            [message.role for message in context.messages],
            ["system", "user", "assistant", "user"],
        )
        self.assertEqual(context.messages[0].content, "测试人格")
        self.assertEqual(context.messages[1].content, "历史问题")
        self.assertEqual(context.messages[2].content, "历史回复")
        self.assertEqual(context.messages[-1].content, "测试")
        self.assertIsInstance(runner.stats, AgentStats)
        self.assertGreater(runner.stats.start_time, 0)

        async def responses():
            if streaming:
                yield LLMResponse(role="assistant", completion_text="测试", is_chunk=True)
            yield LLMResponse(role="assistant", completion_text="测试回复")

        with patch.object(runner, "_iter_llm_responses_with_fallback", responses):
            results = [response async for response in runner.step()]
        self.assertTrue(runner.done())
        self.assertEqual(runner.get_final_llm_resp().completion_text, "测试回复")
        self.assertTrue(any(response.type == "agent_stats" for response in results))

    async def test_full_schema_initializes_text_only_requests(self):
        # 4.29.3 returns early from reset in skills_like mode with no tools,
        # skipping both request messages and stats. Full mode is the deployment workaround.
        for streaming in (False, True):
            for tools in (None, ToolSet()):
                with self.subTest(streaming=streaming, tools=tools):
                    await self.assert_text_only_request("full", tools, streaming)

    # Known upstream defect in pinned AstrBot 4.29.3. Assert the desired behavior,
    # not missing attributes. Once fixed, unittest's unexpected-success result
    # must prompt removal of these decorators and review of the full workaround.
    @unittest.expectedFailure
    async def test_skills_like_initializes_empty_tool_set(self):
        await self.assert_text_only_request("skills_like", ToolSet())

    @unittest.expectedFailure
    async def test_skills_like_initializes_without_tool_set(self):
        await self.assert_text_only_request("skills_like", None)

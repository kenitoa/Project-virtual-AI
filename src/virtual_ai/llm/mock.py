"""Offline fixed response for development without a server or GPU."""


class FakeLLM:
    async def generate(self, messages):
        return (
            "<think>테스트용 내부 문자열</think>안녕하세요! 어떤 이야기를 나눠볼까요?"
        )

    async def aclose(self):
        pass

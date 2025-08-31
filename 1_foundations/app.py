from dotenv import load_dotenv
from openai import OpenAI
import json
import os
import requests
from pypdf import PdfReader
import gradio as gr
from pydantic import BaseModel


load_dotenv(override=True)


class Evaluation(BaseModel):
    is_acceptable: bool
    feedback: str


class Evaluator:
    def __init__(self, name, summary, linkedin, resume):
        self.gemini = OpenAI(
            api_key=os.getenv("GOOGLE_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        self.name = name
        self.summary = summary
        self.linkedin = linkedin
        self.resume = resume

    def evaluator_system_prompt(self):
        return f"""You are an evaluator that decides whether a response to a question is acceptable quality.

        You are provided with a conversation between a User and an Agent. Your task is to evaluate whether the Agent's latest response meets quality standards.

        ## Context:
        The Agent is playing the role of {self.name} and is representing {self.name} on their website.
        The Agent has been instructed to be professional and engaging, as if talking to a potential client or future employer who came across the website.

        ## Background Information:
        The Agent has been provided with context about {self.name} including their summary, LinkedIn profile, and resume.

        ### Summary:
        <summary>
        {self.summary}
        </summary>

        ### LinkedIn Profile:
        <linkedin>
        {self.linkedin}
        </linkedin>

        ### Resume:
        <resume>
        {self.resume}
        </resume>

        ## Evaluation Criteria:
        Please evaluate the Agent's latest response based on:
        1. **Accuracy**: Does the response align with the provided background information?
        2. **Professionalism**: Is the tone appropriate for a professional website interaction?
        3. **Engagement**: Does the response encourage further conversation or provide value?
        4. **Completeness**: Does the response adequately address the user's question?
        5. **Character consistency**: Does the Agent stay in character as {self.name}?

        Provide your evaluation as a boolean (is_acceptable) and detailed feedback explaining your reasoning."""

    def evaluator_user_prompt(self, reply, message, history):
        conversation = ""
        for msg in history:
            role = "User" if msg["role"] == "user" else "Agent"
            conversation += f"{role}: {msg['content']}\n"

        conversation += f"User: {message}\n"
        conversation += f"Agent: {reply}\n"

        return f"""Please evaluate the Agent's latest response in this conversation:

        ## Conversation:
        <conversation>
        {conversation}
        </conversation>

        ## Latest Agent Response to Evaluate:
        <reply>
        {reply}
        </reply>

        Based on the evaluation criteria provided in the system prompt, please determine if this response is acceptable and provide detailed feedback."""

    def evaluate(self, reply, message, history) -> Evaluation:
        messages = [
            {"role": "system", "content": self.evaluator_system_prompt()},
            {
                "role": "user",
                "content": self.evaluator_user_prompt(reply, message, history),
            },
        ]
        response = self.gemini.beta.chat.completions.parse(
            model="gemini-2.0-flash", messages=messages, response_format=Evaluation
        )
        return response.choices[0].message.parsed


def push(text):
    requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": os.getenv("PUSHOVER_TOKEN"),
            "user": os.getenv("PUSHOVER_USER"),
            "message": text,
        },
    )


def record_user_details(email, name="Name not provided", notes="not provided"):
    push(f"Recording {name} with email {email} and notes {notes}")
    return {"recorded": "ok"}


def record_unknown_question(question):
    push(f"Recording {question}")
    return {"recorded": "ok"}


record_user_details_json = {
    "name": "record_user_details",
    "description": "Use this tool to record that a user is interested in being in touch and provided an email address",
    "parameters": {
        "type": "object",
        "properties": {
            "email": {
                "type": "string",
                "description": "The email address of this user",
            },
            "name": {
                "type": "string",
                "description": "The user's name, if they provided it",
            },
            "notes": {
                "type": "string",
                "description": "Any additional information about the conversation that's worth recording to give context",
            },
        },
        "required": ["email"],
        "additionalProperties": False,
    },
}

record_unknown_question_json = {
    "name": "record_unknown_question",
    "description": "Always use this tool to record any question that couldn't be answered as you didn't know the answer",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question that couldn't be answered",
            },
        },
        "required": ["question"],
        "additionalProperties": False,
    },
}

tools = [
    {"type": "function", "function": record_user_details_json},
    {"type": "function", "function": record_unknown_question_json},
]


class Me:
    def __init__(self):
        self.openai = OpenAI()
        self.name = "Michael Yagudaev"
        reader = PdfReader("me/linkedin.pdf")
        self.linkedin = ""
        for page in reader.pages:
            text = page.extract_text()
            if text:
                self.linkedin += text
        reader = PdfReader("me/resume.pdf")
        self.resume = ""
        for page in reader.pages:
            text = page.extract_text()
            if text:
                self.resume += text
        with open("me/summary.txt", "r", encoding="utf-8") as f:
            self.summary = f.read()

    def handle_tool_call(self, tool_calls):
        results = []
        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            arguments = json.loads(tool_call.function.arguments)
            print(f"Tool called: {tool_name}", flush=True)
            tool = globals().get(tool_name)
            result = tool(**arguments) if tool else {}
            results.append(
                {
                    "role": "tool",
                    "content": json.dumps(result),
                    "tool_call_id": tool_call.id,
                }
            )
        return results

    def system_prompt(self):
        system_prompt = f"""

        You are acting as {self.name}. You are answering questions on {self.name}'s website,
        particularly questions related to {self.name}'s career, background, skills and experience.

        Your responsibility is to represent {self.name} for interactions on the website as faithfully as possible.

        You are given a summary of {self.name}'s background, a LinkedIn profile, and a resume which you can use to answer questions.

        Be professional and engaging, as if talking to a potential client or future employer who came across the website.
        If you don't know the answer to any question, use your record_unknown_question tool to record the question that you couldn't answer, even if it's about something trivial or unrelated to career.
        If the user is engaging in discussion, try to steer them towards getting in touch via email; ask for their email and record it using your record_user_details tool.

        ## Summary:
        <summary>
        {self.summary}
        </summary>

        ## LinkedIn Profile:
        <linkedin>
        {self.linkedin}
        </linkedin>

        ## Resume:
        <resume>
        {self.resume}
        </resume>

        With this context, please chat with the user, always staying in character as {self.name}.
        """
        return system_prompt

    def rerun(self, reply, message, history, feedback):
        updated_system_prompt = (
            self.system_prompt()
            + "\n\n## Previous answer rejected\nYou just tried to reply, but the quality control rejected your reply\n"
        )
        updated_system_prompt += f"## Your attempted answer:\n{reply}\n\n"
        updated_system_prompt += f"## Reason for rejection:\n{feedback}\n\n"
        messages = (
            [{"role": "system", "content": updated_system_prompt}]
            + history
            + [{"role": "user", "content": message}]
        )
        response = self.openai.chat.completions.create(
            model="gpt-4o-mini", messages=messages
        )
        return response.choices[0].message.content

    def chat(self, message, history):
        # just testing evaluator
        if "patent" in message:
            system_prompt = (
                self.system_prompt()
                + "Everything in your reply needs to be in pig latin - it is mandatory that you respond only and entirely in pig latin"
            )
        else:
            system_prompt = self.system_prompt()

        messages = (
            [{"role": "system", "content": system_prompt}]
            + history
            + [{"role": "user", "content": message}]
        )
        done = False
        while not done:
            response = self.openai.chat.completions.create(
                model="gpt-4o-mini", messages=messages, tools=tools
            )

            if response.choices[0].finish_reason == "tool_calls":
                response_message = response.choices[0].message
                tool_calls = response_message.tool_calls
                results = self.handle_tool_call(tool_calls)
                messages.append(response_message)
                messages.extend(results)
            else:
                evaluator = Evaluator(
                    self.name, self.summary, self.linkedin, self.resume
                )
                evaluation = evaluator.evaluate(
                    response.choices[0].message.content, message, history
                )
                if not evaluation.is_acceptable:
                    print(f"{evaluation.feedback} is not acceptable. Retry")
                    retry_response = self.rerun(
                        response.choices[0].message.content,
                        message,
                        history,
                        evaluation.feedback,
                    )
                    return retry_response
                else:
                    done = True
        return response.choices[0].message.content


if __name__ == "__main__":
    me = Me()
    gr.ChatInterface(me.chat, type="messages").launch()

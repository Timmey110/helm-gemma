"""
Run one demo task in the terminal and print every event.

  python run_task.py              runs ai-water
  python run_task.py ai-tutors    runs another demo task by id

Gates are answered automatically with the first option.
"""

import asyncio
import json
import sys

from backend import agent
from backend.events import bus


async def main(task_id):
    with open("demo_tasks.json", encoding="utf-8") as file:
        tasks = json.load(file)["tasks"]
    task = next(t for t in tasks if t["id"] == task_id)

    bus.printing = True
    run = agent.Run(task["prompt"])
    run.auto_answer = True
    agent.runs[run.run_id] = run

    # Send the rehearsed steer line a few seconds in, like a person would.
    async def steer_later():
        await asyncio.sleep(5)
        message = {"text": task["steer_message"], "source": "ui"}
        run.inbox.append(message)
        bus.emit(run.run_id, "steer_received", message["text"], message)

    asyncio.create_task(steer_later())
    await agent.run_agent(run)
    await asyncio.sleep(2)  # let the last narration finish
    print(f"\nSaved to runs/{run.run_id}.json")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ai-water"))

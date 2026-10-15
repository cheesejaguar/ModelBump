// Model routing for the web app, served through OpenRouter.
import OpenAI from "openai";

const openrouter = new OpenAI({ baseURL: "https://openrouter.ai/api/v1" });

export const MODELS = {
  chat: "anthropic/claude-sonnet-5",
  bulk: "deepseek/deepseek-v4-flash",
} as const;

export async function chat(prompt: string) {
  return openrouter.chat.completions.create({
    model: MODELS.chat,
    messages: [{ role: "user", content: prompt }],
  });
}

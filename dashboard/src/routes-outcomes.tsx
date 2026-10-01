import type { ViewSpec } from "./routes";
import Guardrails from "./views/Guardrails";
import Intent from "./views/Intent";
import Interventions from "./views/Interventions";
import Provider from "./views/Provider";
import Recurrence from "./views/Recurrence";

/** Guardrail, provider and learning-loop views (V6 to V10). */
export const OUTCOME_VIEWS: ViewSpec[] = [
  {
    id: "guardrails",
    number: "V6",
    title: "Guardrails",
    question:
      "How often do approvals block tool calls, and how often are quality gates bypassed?",
    component: Guardrails,
  },
  {
    id: "provider",
    number: "V7",
    title: "Provider",
    question: "Are model calls failing, slow, or retried?",
    component: Provider,
  },
  {
    id: "recurrence",
    number: "V8",
    title: "Recurrence",
    question:
      "Which files and failures recur across tasks, and which cross the threshold for intervention?",
    component: Recurrence,
  },
  {
    id: "intent",
    number: "V9",
    title: "Intent and corrections",
    question:
      "What kind of work do users ask for, and how often does their next prompt correct the agent?",
    component: Intent,
  },
  {
    id: "interventions",
    number: "V10",
    title: "Interventions",
    question:
      "Which findings became interventions, and did applied interventions reduce what they targeted?",
    component: Interventions,
  },
];

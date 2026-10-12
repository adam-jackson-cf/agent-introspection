import type { ViewSpec } from "./routes";
import Guardrails from "./views/Guardrails";
import Intent from "./views/Intent";
import Interventions from "./views/Interventions";
import Provider from "./views/Provider";
import Recurrence from "./views/Recurrence";

/** Guardrail, provider and learning-loop views. */
export const OUTCOME_VIEWS: ViewSpec[] = [
  {
    id: "guardrails",
    title: "Guardrails",
    question:
      "How often do approvals block tool calls, and how often are quality gates bypassed?",
    component: Guardrails,
  },
  {
    id: "provider",
    title: "Provider",
    question: "Are model calls failing, slow, or retried?",
    component: Provider,
  },
  {
    id: "recurrence",
    title: "Recurring failures",
    question:
      "Which files and failures recur across tasks, and which cross the threshold for intervention?",
    component: Recurrence,
  },
  {
    id: "intent",
    title: "Requests and corrections",
    question:
      "What kind of work do users ask for, and how often does their next prompt correct the agent?",
    component: Intent,
  },
  {
    id: "interventions",
    title: "Findings and fixes",
    question:
      "Which findings became interventions, and did applied interventions reduce what they targeted?",
    component: Interventions,
  },
];

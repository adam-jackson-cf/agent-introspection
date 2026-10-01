import type { ViewSpec } from "./routes";
import Cache from "./views/Cache";
import Effort from "./views/Effort";
import Friction from "./views/Friction";
import Pipeline from "./views/Pipeline";
import Tools from "./views/Tools";

/** Pipeline freshness, cost and friction views (V1 to V5). */
export const HEALTH_VIEWS: ViewSpec[] = [
  {
    id: "pipeline",
    number: "V1",
    title: "Pipeline",
    question:
      "Is the fact loader fresh, complete, and correct for every harness?",
    component: Pipeline,
  },
  {
    id: "cache",
    number: "V2",
    title: "Cache efficiency",
    question:
      "Where are tokens consumed, and how much input is served from cache?",
    component: Cache,
  },
  {
    id: "effort",
    number: "V3",
    title: "Reasoning effort",
    question: "Does heavier reasoning effort earn its cost?",
    component: Effort,
  },
  {
    id: "tools",
    number: "V4",
    title: "Tool failures",
    question:
      "Which tools fail, how often, in which tasks, and do they repeat or loop?",
    component: Tools,
  },
  {
    id: "friction",
    number: "V5",
    title: "Friction",
    question:
      "How often do users interrupt, steer, or follow up quickly, and do tasks finish cleanly?",
    component: Friction,
  },
];

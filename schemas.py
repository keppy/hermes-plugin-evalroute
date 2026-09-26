"""Tool schemas for the evalroute plugin — what the model reads."""

EVALROUTE_ROUTE = {
    "name": "evalroute_route",
    "description": (
        "Route a task to the right (model, reasoning effort) arm before starting work. "
        "Use when the user asks which model to use for a task, before a long or expensive "
        "session, when switching models mid-project, or when planning subagent delegation. "
        "Returns a route card with the lane, the model, the effort, the escalation option, "
        "and the provenance of the recommendation. Classification is deterministic keyword "
        "rules over a measured route table; it costs nothing and makes no API calls."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task description to classify. A short phrase or the "
                               "first sentence is enough; the classifier matches keywords.",
            },
            "lane": {
                "type": "string",
                "description": "Optional: pin the lane explicitly (one of the route table's "
                               "lane ids, e.g. 'math-first-principles') to skip classification.",
            },
        },
        "required": ["task"],
    },
}

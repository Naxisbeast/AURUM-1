# ML Added No Edge

**What happened**: I spent weeks building a machine learning pipeline — RegimeClassifier, DirectionPredictor, SentimentScorer, weekly retraining schedule. I genuinely believed this would be the system's edge.

**Impact**: The ML ensemble (D6) produced a profit factor of 1.14 over 11 years. D4 (no ML) also produced 1.14. The ML added zero measurable value.

**Fix**: All ML models disabled in production. Settings locked to `enable_direction_predictor: false`.

**Lesson**: Complexity doesn't deserve a seat at the table unless it can justify its existence. ML is a tool, not a goal.

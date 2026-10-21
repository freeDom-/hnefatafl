# Project: Hnefatafl — Cross-Platform Viking Board Game with AI and Machine Learning

## 1. Mission and Autonomy

Develop a complete, playable, extensible, and well-tested implementation of **Hnefatafl**, the historical Viking board game, including multiple rulesets, computer opponents, game statistics, and a comprehensive AI training framework.

**You are responsible for the entire development process**, including architecture decisions, implementation, configuration, dependency management, testing, debugging, documentation, and local deployment.

Do not stop after creating an implementation plan, scaffolding, or prototype. Continue implementing, testing, fixing issues, and improving the application until all requirements of the initial release are fulfilled or you encounter a genuine external blocker.

Work autonomously:

- Make reasonable technical and design decisions without asking for confirmation.
- Research established solutions and reuse maintained libraries where appropriate.
- Prefer robust, maintainable implementations over unnecessary complexity.
- Avoid placeholders, unimplemented functions, mock implementations of core features, and TODOs for functionality required in the initial release.
- Execute available tests and fix failures before proceeding.
- Where possible, test the actual application rather than relying exclusively on unit tests.
- Keep track of progress against the requirements.
- Commit logically separated changes when Git is available and configured.
- Document decisions, limitations, assumptions, and significant architectural trade-offs.
- Do not consider a feature complete until it works and has appropriate tests.
- If hardware, permissions, or external services are unavailable, implement everything that can be completed and clearly report which validations remain unverified.

**The goal is a usable software product, not a proof of concept.**

## 2. Technology and Architecture

Evaluate the most appropriate programming languages, frameworks, and libraries.

My suggested architecture is:

- **Frontend:** TypeScript, React, Vite, responsive web application or Progressive Web App (PWA).
- **Backend:** Python with FastAPI.
- **Game engine:** Python, with deterministic, independently testable game logic.
- **Machine learning:** PyTorch.
- **Hyperparameter optimization:** Optuna.
- **Database:** SQLite for simple installations, with a migration path to PostgreSQL.
- **Deployment:** Self-hosted Linux server, preferably compatible with a Proxmox LXC environment.

You may choose alternatives if they demonstrably improve the project. Explain your choices in an architecture document.

Important architectural requirements:

1. The application must run in modern desktop and mobile browsers.
2. The user interface must adapt to different screen sizes and input methods.
3. Game rules, AI algorithms, training, persistence, and UI should be clearly separated.
4. Each ruleset must be configurable through a shared game-engine abstraction rather than implemented as a completely separate game.
5. The architecture must support future internet multiplayer without requiring a major redesign.
6. Game state and moves should use a consistent, serializable format.
7. AI models and training components must be decoupled from the web frontend.
8. The training system must support headless execution on a machine with a GPU.
9. The game server must not be blocked by long-running AI training jobs.
10. The application must be easy to install, configure, update, and start.

A browser-based application is preferred over separate native Windows, Android, and iOS implementations.

An internet connection to a self-hosted server is acceptable for the initial release. Full offline play is desirable but not mandatory.

## 3. Supported Rulesets

Implement the following variants of Hnefatafl.

### Copenhagen Hnefatafl

Reference:
[https://aagenielsen.dk/copenhagen_rules.php](https://aagenielsen.dk/copenhagen_rules.php)

### Brandubh

Reference:
[http://www.cyningstan.com/data-download/395/brandub-leaflet](http://www.cyningstan.com/data-download/395/brandub-leaflet)

### Tablut

Reference:
[https://aagenielsen.dk/tablutmarketregler_engelsk.pdf](https://aagenielsen.dk/tablutmarketregler_engelsk.pdf)

### Ruleset implementation requirements

Read and analyze the referenced rules carefully before implementing them.

Each ruleset must correctly define its own:

- Board dimensions.
- Initial piece arrangement.
- Attacker and defender pieces.
- Legal piece movement.
- Capture mechanics.
- King movement and capture conditions.
- Victory conditions for attackers and defenders.
- Special squares and movement restrictions.
- Special capture mechanisms, where applicable.
- Draw conditions and repetition rules.
- Other variant-specific rules.

Pay particular attention to the asymmetric nature of Hnefatafl and differences between variants.

Do not assume that all Hnefatafl variants share identical rules.

For Copenhagen, explicitly investigate and implement its special mechanisms, including shield-wall captures, encirclement, and edge-fort rules where required by the referenced specification.

If any rule is ambiguous or historical sources differ, prefer the linked ruleset definition, document the interpretation, and create tests demonstrating the chosen behavior.

Build the engine around configurable ruleset definitions, using extensible interfaces for rules that cannot be represented by simple configuration.

### Ruleset documentation

Provide dedicated documentation for each variant, containing:

- Historical background.
- Board layout and initial positions.
- Movement rules.
- Capture rules.
- Victory and draw conditions.
- Special rules and examples.
- Differences from other implemented variants.
- Links to original sources.
- Notes on any ambiguities or implementation decisions.

The rules must also be accessible from within the game interface.

## 4. Gameplay and User Interface

Create an attractive, intuitive interface with a visual design inspired by Viking board games.

The interface should be pleasant to use on both phones and PCs.

### Supported gameplay modes

Implement these modes:

1. **Human vs Human:** Two players using the same device.
2. **Human vs AI:** A human plays against a selected computer opponent.
3. **AI vs AI:** Two independently configured computer opponents play against each other.
4. **Online Human vs Human:** Planned for a later version, but considered in the initial architecture.

### Game interface

Implement:

- Ruleset selection before starting a game.
- Gameplay mode selection.
- Side selection (attackers or defenders).
- AI selection and configuration.
- Clear visual distinction between attackers, defenders, and king.
- Indication of the active player.
- Selection and highlighting of legal moves.
- Move validation and rejection of illegal moves.
- Captured-piece visualization.
- Move history.
- Undo/redo support where appropriate for local games.
- Restart and new-game functionality.
- Clear game-over messages.
- Responsive layout for touchscreens, mouse, and keyboard.
- Easy access to the active ruleset documentation.

For AI vs AI:

- Show the game progressing automatically.
- Allow adjusting the playback speed.
- Allow pausing and resuming.
- Show which AI is currently calculating.
- Display move count and relevant game information.

The visual design should be polished without compromising usability or development priorities.

## 5. AI Opponents

AI is a major part of this project.

Design an extensible AI architecture so that new algorithms, strategies, difficulty levels, and trained neural networks can be added without modifying the core game engine.

### 5.1 Baseline AI

Implement at least one functional baseline AI for each supported ruleset.

It should:

- Generate only legal moves.
- Understand basic tactical threats.
- Consider the asymmetric objectives of attackers and defenders.
- Use a ruleset-aware evaluation function.
- Be capable of completing full games.
- Make decisions within a reasonable time.
- Provide a baseline for evaluating more advanced AI models.

A heuristic-based AI is acceptable for this purpose.

### 5.2 Configurable Search AI

Implement a second, configurable AI framework that supports multiple decision-making algorithms.

At minimum, support:

- Alpha-beta search, with configurable search depth.
- Monte Carlo Tree Search (MCTS), with configurable simulation or iteration budget.
- Optional neural-network-assisted evaluation.

The user should be able to configure relevant parameters such as:

- Search algorithm.
- Search depth or computation budget.
- Time limit.
- Exploration parameters.
- Evaluation method.
- Neural network/model selection, where supported.
- Randomness or temperature, where applicable.

Search algorithms must be real, working implementations rather than interface stubs.

The AI framework must work with all three rulesets.

### 5.3 AI Profiles and Difficulty

Create a registry of AI players or profiles.

Each AI profile should describe:

- Name.
- Algorithm.
- Strategy or evaluation function.
- Difficulty.
- Supported rulesets.
- Configurable parameters.
- Optional neural network.

Allow players to select AI opponents from the UI.

Additional strategies and difficulty presets can be added later, but the initial implementation must demonstrate a functioning baseline AI and configurable search-based AI for every ruleset.

### 5.4 AI Evaluation

Implement a framework for comparing AI opponents.

It should support:

- AI vs AI tournaments.
- Multiple games with alternating sides.
- Win, loss, and draw statistics.
- Average game length.
- Average decision time.
- Comparative performance across rulesets.
- Reproducible experiments using configurable random seeds.

Make AI performance measurable rather than relying exclusively on subjective observations.

## 6. Neural Network Training Framework

**This is a high-priority feature and must be treated as a first-class component of the project, not an optional experiment.**

Build a complete workflow that allows a user to train neural networks, evaluate them, save them, and import them into the game as playable AI opponents.

The training system should be accessible to users without requiring them to write Python code.

### 6.1 Training Approaches

Evaluate suitable machine-learning approaches for Hnefatafl.

Prefer a practical self-play reinforcement-learning approach inspired by AlphaZero, combining:

- A policy/value neural network.
- Self-play game generation.
- Monte Carlo Tree Search where appropriate.
- Model optimization from generated game data.
- Periodic evaluation against baseline and previous models.

The system must be able to train models from scratch without requiring an existing external training dataset.

A supervised training option based on imported recorded games is desirable, but not essential for the initial version.

Training must support all implemented rulesets.

Determine whether separate models per ruleset or a shared ruleset-aware model is more appropriate. Document the decision.

The training architecture should allow future experimentation with other neural network architectures and training methods.

### 6.2 GPU Acceleration

GPU acceleration is mandatory as a supported capability.

Requirements:

- NVIDIA CUDA support using PyTorch.
- Automatic detection of available compatible hardware.
- CPU fallback.
- Configurable device selection.
- Configurable training batch size.
- Mixed-precision training where supported and beneficial.
- Efficient neural network inference during self-play.
- Efficient utilization of GPU resources.
- Optional support for AMD ROCm and Apple Metal/MPS where feasible.

Avoid designs that unnecessarily transfer data between CPU and GPU.

Ensure multiprocessing, model inference, and training processes are compatible with CUDA.

The implementation must work on CPU-only systems, although training may be significantly slower.

If no supported GPU is available in the development environment, perform CPU-based functional tests and report GPU verification as pending rather than claiming it was tested.

### 6.3 Training Configuration

Provide a simple configuration system for training jobs.

Expose at least:

- Ruleset.
- Neural network architecture or preset.
- Number of self-play games.
- Training iterations.
- Number of epochs.
- Batch size.
- Learning rate.
- Optimizer.
- MCTS parameters.
- Exploration settings.
- Replay buffer size.
- Regularization parameters.
- Checkpoint frequency.
- Evaluation frequency.
- Random seed.
- CPU/GPU device.
- Training output directory.

Use sensible defaults and provide beginner-friendly presets.

A user should be able to initiate a basic training session by selecting a ruleset and a training preset.

Advanced users should be able to modify individual parameters.

### 6.4 Automated Hyperparameter Optimization

Implement an automated hyperparameter optimization workflow using Optuna or an equivalent established library.

It should support:

- Configurable parameter search spaces.
- Configurable optimization budgets.
- Automatic trial execution.
- Trial pruning or early stopping where appropriate.
- Tracking of results across trials.
- Reproducible configurations.
- Saving the best-performing configuration.
- Resuming interrupted studies.

Use meaningful model evaluation metrics rather than training loss alone when selecting the best configuration.

Document the computational cost and trade-offs of automated tuning.

### 6.5 Training Monitoring and Management

Provide an easy way to:

- Start a training job.
- View training progress.
- Inspect metrics and logs.
- View GPU/device information.
- Stop or cancel a job.
- Resume training from a checkpoint.
- Compare trained models.
- Inspect evaluation results.

A web-based training management interface is preferred.

If implementing a complete training dashboard becomes disproportionate, prioritize a fully working CLI with configuration files and a lightweight web interface for starting and monitoring jobs.

Include metrics such as:

- Policy and value losses.
- Games generated.
- Training iterations.
- Win/draw/loss ratios against baseline opponents.
- Training and evaluation durations.
- Best model checkpoint.
- GPU memory usage where available.

TensorBoard integration is desirable.

### 6.6 Model Storage and Import

Implement a model registry.

Each model should include metadata such as:

- Model name and version.
- Compatible ruleset.
- Neural network architecture.
- Training configuration.
- Training date.
- Training metrics.
- Evaluation results.
- Model/checkpoint format.

Users must be able to:

1. Train a model.
2. Export or save the model.
3. Import an existing compatible model.
4. Register the model as an AI player.
5. Select the model in Human vs AI or AI vs AI modes.
6. Use the model with a supported search algorithm.

Validate imported model compatibility and fail safely on invalid input.

Avoid unsafe deserialization of arbitrary model files.

### 6.7 Training Verification

Include an automated end-to-end smoke test that:

1. Initializes a new neural network.
2. Generates a small amount of self-play data.
3. Performs an actual training update.
4. Saves a checkpoint.
5. Reloads the checkpoint.
6. Evaluates the resulting model.
7. Registers the model.
8. Uses it to play legal moves in a complete game.

Also test checkpoint resumption and hyperparameter optimization using a small, affordable trial budget.

Do not require GPU hardware or lengthy training runs for the default automated test suite.

Full training quality must be evaluated separately from functional correctness. Do not claim that a model has become stronger unless evaluation results support that conclusion.

## 7. Game Persistence and Statistics

Design a persistent game history and statistics system.

### Player Profiles

Support local player profiles initially.

Profiles should have a stable identity and display name.

Prepare the architecture for future authenticated online accounts.

### Persisted Games

Store relevant data such as:

- Game ID.
- Ruleset and ruleset version.
- Players or AI profiles.
- Initial game configuration.
- Moves.
- Final result.
- Winner.
- Number of moves.
- Start and end timestamps.
- Relevant AI configuration.

Allow saved games to be reconstructed and replayed from their recorded moves.

The game engine should support deterministic replay and validation of stored games.

### Statistics

Provide statistics for:

- Individual players.
- Individual AI profiles and models.
- Each ruleset.
- Each gameplay mode.
- Attackers versus defenders.
- Historical results.
- Win/loss/draw ratios.
- Average game length.

Include useful filters and an intuitive statistics dashboard.

Clearly distinguish human statistics from AI statistics.

### Future Online Multiplayer

Online multiplayer can be postponed until a later release.

However, design the initial architecture with:

- Server-authoritative game state.
- Persistent game sessions.
- Stable player identities.
- APIs for creating, joining, and updating games.
- Protection against illegal and out-of-turn moves.
- Concurrency-safe move processing.
- A future real-time communication mechanism such as WebSockets.

When online multiplayer is implemented, games must be hosted on a server, and their histories and results must be stored centrally.

Do not create a networking architecture that would require rewriting the game engine to add multiplayer support.

## 8. Testing and Quality Assurance

Implement meaningful automated tests throughout the project.

### Game Engine

Test:

- Initial board positions.
- Legal and illegal movement.
- Normal captures.
- King captures.
- Special captures.
- Special-square restrictions.
- Victory and draw conditions.
- Edge cases for every ruleset.
- Repeated positions and other applicable special rules.
- Deterministic game replay.
- State serialization and deserialization.

Use separate, ruleset-specific fixtures and test cases.

### AI

Test:

- Legal move generation.
- Alpha-beta search.
- MCTS.
- Heuristic evaluation.
- Model-assisted move selection.
- Valid behavior under time or search limits.
- Completion of AI vs AI games.
- Model compatibility and loading.

### Training

Test:

- Self-play data generation.
- Neural network forward and backward passes.
- Training updates.
- Checkpoint creation and loading.
- Training resumption.
- Model registration.
- Hyperparameter tuning.
- CPU execution.
- GPU execution when suitable hardware is available.

### Application

Test:

- Gameplay API endpoints.
- Persistence and game reconstruction.
- Statistics calculations.
- Main frontend flows.
- Game completion.
- AI selection.
- Mobile-responsive interaction.
- Training configuration and model selection.

Use established tools such as pytest, Vitest, and Playwright, or suitable equivalents.

Add formatting, linting, and type-checking tools.

Provide one or more commands to run the complete relevant test suites.

Where possible, include continuous integration using GitHub Actions.

## 9. Documentation

Create comprehensive documentation in Markdown.

At minimum, include:

**README.md**

- Project overview.
- Features.
- Screenshots if possible.
- Installation.
- Quick start.
- Development setup.
- Testing.
- Deployment.
- Current limitations.

**ARCHITECTURE.md**

- System components.
- Technology choices.
- Game-engine architecture.
- Ruleset abstraction.
- AI interfaces.
- Training system.
- Database design.
- Future multiplayer architecture.

**RULESETS.md**, or individual ruleset documents

- Complete rules and examples.
- References.
- Variant-specific implementation decisions.

**AI.md**

- Available AI implementations.
- AI configuration.
- Search algorithms.
- Evaluation functions.
- Adding new AI implementations.

**TRAINING.md**

- Hardware and software requirements.
- Installing GPU support.
- Training from scratch.
- Training presets.
- Advanced configuration.
- Hyperparameter optimization.
- Monitoring training.
- Resuming interrupted training.
- Evaluating models.
- Exporting and importing models.
- Troubleshooting.

**DEPLOYMENT.md**

- Local installation.
- Production configuration.
- Linux self-hosting.
- Database setup and migration.
- Persistent storage and backups.
- Updating the application.
- Running behind a reverse proxy.
- Security considerations.

Document the actual implementation, not merely planned functionality.

## 10. Installation and Deployment

Provide a reproducible installation and startup workflow.

Requirements:

- Clear dependency declarations.
- Configuration through environment variables or configuration files.
- Example configuration.
- Automated initialization where practical.
- Database migrations.
- Development startup commands.
- Production startup instructions.
- Persistent storage for game data and model files.
- Separation of the game service and computationally expensive training jobs.

Prefer a setup that can be hosted on an ordinary Linux server or Proxmox LXC.

Containerized deployment is acceptable, but do not make Docker mandatory unless there is a compelling reason.

Provide a simple method for starting services automatically after reboot, such as systemd or an appropriate container orchestration configuration.

Do not embed secrets or machine-specific paths in the code.

## 11. Development Workflow and Priorities

Work through the following milestones. These are execution stages, not reasons to stop and request approval.

### Milestone 1 — Architecture and Foundation

- Inspect the repository and existing files.
- Research the specified rulesets.
- Choose and document the architecture.
- Create the project structure.
- Configure dependencies, development commands, and testing infrastructure.

### Milestone 2 — Complete Game Engine

- Implement all three rulesets.
- Implement move generation, captures, terminal states, and serialization.
- Build extensive ruleset-specific tests.

### Milestone 3 — Playable Application

- Build the responsive game interface.
- Implement Human vs Human.
- Connect the game engine to the frontend.
- Implement local game sessions and persistence.

### Milestone 4 — Traditional AI

- Implement heuristic AI.
- Implement alpha-beta and MCTS.
- Implement Human vs AI and AI vs AI.
- Build AI configuration and evaluation tools.

### Milestone 5 — Neural Network Training

- Implement the policy/value model.
- Implement self-play data generation.
- Implement training, checkpointing, and model evaluation.
- Integrate NVIDIA CUDA and CPU fallback.
- Implement hyperparameter optimization.
- Implement the model registry.
- Make trained models selectable as AI opponents.

### Milestone 6 — Statistics and Usability

- Complete game history and replay.
- Implement player, AI, and ruleset statistics.
- Improve UI interactions and mobile usability.
- Add training monitoring and management.

### Milestone 7 — Verification and Documentation

- Run automated test suites.
- Run end-to-end gameplay tests.
- Execute a small complete training workflow.
- Test model import and AI gameplay with the trained model.
- Validate production build and startup.
- Fix discovered defects.
- Complete all documentation.

You may revise the implementation order if it improves efficiency or reduces technical risk, but all initial-release requirements remain mandatory.

Maintain a progress checklist in the repository and update it as work proceeds.

**Do not stop after any milestone solely to ask whether you should continue.**

## 12. Definition of Done

The initial release is complete only when:

- [ ] The application can be installed and started using documented commands.
- [ ] The game is usable on desktop and mobile browsers.
- [ ] Copenhagen, Brandubh, and Tablut are fully implemented and tested.
- [ ] Human vs Human works.
- [ ] Human vs AI works for all rulesets.
- [ ] AI vs AI works for all rulesets.
- [ ] A baseline AI is available for each ruleset.
- [ ] Configurable alpha-beta and MCTS opponents are available.
- [ ] Neural networks can be trained from scratch.
- [ ] NVIDIA CUDA support is implemented, with CPU fallback.
- [ ] Automated hyperparameter optimization works.
- [ ] Training checkpoints can be saved and resumed.
- [ ] Trained models can be imported and selected as AI opponents.
- [ ] Model-assisted AI completes valid games.
- [ ] Game history and player statistics are persisted.
- [ ] Statistics are available per ruleset and AI.
- [ ] The architecture supports future server-hosted internet multiplayer.
- [ ] Automated tests pass in the available environment.
- [ ] An end-to-end training smoke test passes.
- [ ] A production build can be started successfully.
- [ ] Installation, gameplay, AI, training, and deployment are documented.

Internet multiplayer itself is not required for this initial release.

Do not mark hardware-dependent tests as passed unless they were actually executed on compatible hardware.

## 13. Final Deliverables and Report

Upon completing the implementation, provide:

1. A summary of the implemented functionality.
2. The chosen technology stack and the reasoning behind it.
3. The repository structure.
4. Exact installation and startup commands.
5. Instructions for playing locally and accessing the game from a phone.
6. Instructions for training and importing AI models.
7. Results from executed automated tests.
8. Results of the training smoke test.
9. Any remaining limitations or unverified hardware-dependent functionality.
10. Recommended next steps for improving AI strength and implementing online multiplayer.

Whenever a requested feature cannot be completed, report exactly what remains, why it is blocked, and what is needed to finish it. Do not silently replace it with a mock or placeholder.

**Begin implementation immediately. Work autonomously through the milestones and continue until the initial release meets the Definition of Done, subject only to genuine limitations of the execution environment.**

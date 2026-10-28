"""业务实体层（Entity）：纯数据类，用于业务层传递，脱离 ORM 依赖。

与 model 层的区别：
- model 层是 SQLAlchemy ORM 模型，与数据库表一一对应，负责持久化
- entity 层是 Pydantic dataclass，是业务领域的数据载体，不依赖数据库框架

这样设计让业务逻辑层不直接耦合 ORM，便于测试和替换持久化实现。
"""

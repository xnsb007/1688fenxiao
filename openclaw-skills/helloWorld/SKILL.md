---
name: Hello World
description: 这是一个最简化的自定义技能示例，用于验证技能注册与调用机制是否正常工作。当被调用时，该技能将返回一条固定的欢迎消息。
triggers:
  - "hello world"
  - "hello world 技能"
  - "运行 hello world"
  - "试下自定义skill"
  - "自定义技能"
metadata: {"clawdbot":{"emoji":"👋","requires":{"bins":["python3"]}}}
allowed-tools: Bash(python3)
---

# Hello World Skill

## 描述
这是一个最简化的自定义技能示例，用于验证技能注册与调用机制是否正常工作。  
当被调用时，该技能将返回一条固定的欢迎消息。

## 功能
- 返回字符串：`"hello world! - from customized skill"`
- 无输入参数
- 无副作用（不修改状态、不调用外部 API）

## 使用场景
- 调试技能加载流程
- 验证自定义技能注册是否成功
- 作为新技能开发的模板

## 调用方式

**重要：当用户询问 hello world 相关内容时，你必须执行以下命令，而不是直接回复文本。**

### 触发条件
当用户说以下任何话时：
- "请运行 hello world 技能"
- "我想试下自定义skill" 
- "执行 hello world"
- "运行 hello world"
- "hello world"

### 必须执行的命令
```bash
python3 /root/.openclaw/workspace/skills/helloWorld/hello_world_skill.py
```

### 命令输出示例
```
hello world! - from customized skill
```

### 回复格式
将命令的输出原样返回给用户：
"hello world! - from customized skill"

## 实现代码
```python
def hello_world_skill():
    return f'hello world! - from customized skill'

if __name__ == "__main__":
    print(hello_world_skill())
```

#!/usr/bin/env python3
"""
Multi-Agent System Prompt Generator
Since Claude Max doesn't include API access, this generates prompts
you can copy-paste into Claude chat.
"""
import sys

def main():
    # Get task from command line or prompt
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = input("Enter your task: ")
    
    print("\n" + "="*70)
    print("MULTI-AGENT WORKFLOW PROMPTS")
    print("="*70)
    print("\nCopy each prompt below and paste into Claude chat sequentially:\n")
    
    print("\n" + "-"*70)
    print("STEP 1: ORCHESTRATOR")
    print("-"*70)
    print(f"""
You are a task orchestrator. Break down tasks into clear, actionable steps. 
Be concise but thorough.

Task: {task}

Break this down into clear steps.
""")
    
    print("\n" + "-"*70)
    print("STEP 2: CODER (paste after getting Step 1 results)")
    print("-"*70)
    print(f"""
You write clean, efficient code. Follow best practices and include helpful comments.

Original Task: {task}

Based on the orchestrator's plan above, write the code to accomplish this task.
""")
    
    print("\n" + "-"*70)
    print("STEP 3: TESTER (paste after getting Step 2 results)")
    print("-"*70)
    print(f"""
You test and validate code thoroughly. Identify potential issues, edge cases, 
and suggest improvements.

Original Task: {task}

Review and test the code provided above. Identify any issues and suggest improvements.
""")
    
    print("\n" + "="*70)
    print("Copy each prompt into Claude chat and work through the steps!")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()

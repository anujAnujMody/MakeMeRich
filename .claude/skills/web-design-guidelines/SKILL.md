---
name: web-design-guidelines
description: Review UI code for Web Interface Guidelines compliance. Use when asked to "review my UI", "check accessibility", "audit design", "review UX", or "check my site against best practices".
metadata:
  author: vercel
  version: "1.0.0"
  argument-hint: <file-or-pattern>
source: official (vercel-labs/agent-skills)
---

# Web Interface Guidelines Review Tool

## Overview

This tool evaluates UI code against established web design standards. It's triggered by requests
to audit interfaces, verify accessibility compliance, or validate design practices.

## Process

The workflow involves three key steps:

1. **Retrieve current guidelines** from the Vercel Labs repository
2. **Process specified files** (or request user input for file selection)
3. **Generate findings** in a concise `file:line` format

## Guidelines Source

Guidelines are fetched dynamically from:
`https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md`

Use WebFetch to obtain the latest ruleset before each review, ensuring evaluations reflect current
standards rather than a stale local copy.

## Implementation

When files are specified:
- Retrieve fresh guidelines
- Analyze the indicated files
- Apply all fetched rules systematically
- Report results in `file:line — issue` format

When files aren't specified, prompt for selection details (which page/component to review) rather
than guessing scope.

## Applied to this project

Run this as a linter pass on each finished page/component before marking a TDD task done — pair it
with the accessibility requirements already called out in the plan (WCAG contrast on loss-red,
colorblind-safe status colors, tabular numerals, no color-alone meaning).

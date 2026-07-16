export type JsonSchemaLiteType = 'string' | 'number' | 'boolean' | 'object' | 'array';

export interface JsonSchemaLiteProperty {
  type: JsonSchemaLiteType;
  description?: string;
  enum?: string[];
  items?: { type: JsonSchemaLiteType };
}

export interface JsonSchemaLite {
  type: 'object';
  properties: Record<string, JsonSchemaLiteProperty>;
  required: string[];
}

export type ToolRisk = 'read' | 'write' | 'exec' | 'destructive';

export interface ToolContext {
  /** Absolute path of the project root; all paths must resolve inside it. */
  projectRoot: string;
  report?: (message: string) => void;
}

export interface ToolResult {
  ok: boolean;
  /** Human/model readable output (stdout, file content, error message...). */
  output: string;
  exitCode?: number;
}

export interface Tool<A = Record<string, unknown>> {
  name: string;
  description: string;
  schema: JsonSchemaLite;
  risk: ToolRisk;
  run(args: A, ctx: ToolContext): Promise<ToolResult>;
}

export interface ValidationIssue {
  path: string;
  message: string;
}

export function validateArgs(
  schema: JsonSchemaLite,
  args: unknown,
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (typeof args !== 'object' || args === null || Array.isArray(args)) {
    return [{ path: '$', message: 'arguments must be a JSON object' }];
  }
  const record = args as Record<string, unknown>;
  for (const name of schema.required) {
    if (!(name in record) || record[name] === undefined || record[name] === null) {
      issues.push({ path: name, message: 'missing required argument' });
    }
  }
  for (const [name, value] of Object.entries(record)) {
    const property = schema.properties[name];
    if (!property) {
      issues.push({ path: name, message: 'unknown argument' });
      continue;
    }
    if (value === undefined || value === null) continue;
    if (!matchesType(value, property.type)) {
      issues.push({ path: name, message: `expected ${property.type}` });
      continue;
    }
    if (property.enum && typeof value === 'string' && !property.enum.includes(value)) {
      issues.push({ path: name, message: `expected one of: ${property.enum.join(', ')}` });
    }
    if (property.type === 'array' && property.items && Array.isArray(value)) {
      for (const [index, item] of value.entries()) {
        if (!matchesType(item, property.items.type)) {
          issues.push({ path: `${name}[${index}]`, message: `expected ${property.items.type}` });
        }
      }
    }
  }
  return issues;
}

function matchesType(value: unknown, type: JsonSchemaLiteType): boolean {
  switch (type) {
    case 'string': return typeof value === 'string';
    case 'number': return typeof value === 'number' && Number.isFinite(value);
    case 'boolean': return typeof value === 'boolean';
    case 'array': return Array.isArray(value);
    case 'object': return typeof value === 'object' && value !== null && !Array.isArray(value);
  }
}

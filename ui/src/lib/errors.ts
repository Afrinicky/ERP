/** Translate a thrown value into a message, and a field where one is named. */

import { ServiceError, ServiceUnreachableError } from '@/api/client';

export interface Problem {
  message: string;
  field: string | null;
}

export function describe(error: unknown): Problem {
  if (error instanceof ServiceError) {
    return { message: error.message, field: error.field };
  }
  if (error instanceof ServiceUnreachableError) {
    return { message: error.message, field: null };
  }
  if (error instanceof Error) {
    return { message: error.message, field: null };
  }
  return { message: 'The operation could not be completed.', field: null };
}

export function fieldError(error: unknown, field: string): string | null {
  const problem = describe(error);
  return problem.field === field ? problem.message : null;
}

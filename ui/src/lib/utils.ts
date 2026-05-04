import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

// Standard shadcn/ui helper: combine clsx + tailwind-merge so duplicate
// utility classes resolve to last-wins. All shadcn components import this.
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

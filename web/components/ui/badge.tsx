import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold",
  {
    variants: {
      variant: {
        default: "bg-primary/10 text-primary ring-1 ring-inset ring-primary/20",
        success: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200",
        warning: "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200",
        danger: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200",
        muted: "bg-muted text-muted-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };

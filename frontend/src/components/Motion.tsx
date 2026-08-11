"use client";

import { motion, useReducedMotion, type MotionProps, type Variants } from "framer-motion";

type FadeInProps = MotionProps & {
  children: React.ReactNode;
  className?: string;
};

type PageTransitionProps = MotionProps & {
  children: React.ReactNode;
  className?: string;
};

export function PageTransition({ children, className, ...props }: PageTransitionProps) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={reduce ? { opacity: 1 } : { opacity: 0, y: 20 }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0 }}
      transition={reduce ? { duration: 0 } : { duration: 0.45, ease: [0.2, 0.8, 0.2, 1] }}
      {...props}
    >
      {children}
    </motion.div>
  );
}

export function FadeIn({
  children,
  className,
  ...props
}: FadeInProps) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={className}
      variants={{
        hidden: reduce ? { opacity: 1 } : { opacity: 0, y: 10 },
        show: {
          opacity: 1,
          y: 0,
          transition: reduce
            ? { duration: 0 }
            : { delayChildren: 0.08, staggerChildren: 0.04 }
        }
      }}
      initial="hidden"
      animate="show"
      transition={reduce ? { duration: 0 } : { duration: 0.4, ease: [0.2, 0.8, 0.2, 1] }}
      {...props}
    >
      {children}
    </motion.div>
  );
}

export const staggerContainer = (stagger = 0.08): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: stagger } }
});

export const fadeUpItem: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0 }
};


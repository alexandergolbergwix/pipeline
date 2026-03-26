#!/usr/bin/env python3
"""Regenerate baseline comparison figures with updated values."""

import matplotlib.pyplot as plt
import numpy as np

# Set style for academic paper
plt.rcParams.update({
    'font.family': 'serif',
    'font.size': 12,
    'axes.labelsize': 14,
    'axes.titlesize': 14,
    'xtick.labelsize': 11,
    'ytick.labelsize': 11,
    'legend.fontsize': 11,
    'figure.figsize': (8, 5),
    'axes.grid': True,
    'grid.alpha': 0.3,
})

def create_three_way_comparison():
    """Create the three-way baseline comparison figure."""
    models = ['Zero-shot\nDictaBERT', 'General NER\nDictaBERT', 'Our Fine-tuned\nJoint Model']
    f1_scores = [1.40, 30.15, 85.70]
    colors = ['#d62728', '#ff7f0e', '#2ca02c']
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    bars = ax.bar(models, f1_scores, color=colors, edgecolor='black', linewidth=1.2)
    
    # Add value labels on bars
    for bar, score in zip(bars, f1_scores):
        height = bar.get_height()
        ax.annotate(f'{score:.2f}%',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha='center', va='bottom',
                    fontsize=14, fontweight='bold')
    
    ax.set_ylabel('Entity-level F1 Score (%)', fontsize=14)
    ax.set_title('Three-Way Baseline Comparison: NER Performance on Hebrew Manuscript Dataset', fontsize=14, pad=15)
    ax.set_ylim(0, 100)
    
    # Add improvement annotations
    ax.annotate('', xy=(1, 30.15), xytext=(0, 1.40),
                arrowprops=dict(arrowstyle='->', color='gray', lw=1.5))
    ax.text(0.5, 15, '+28.75%', ha='center', fontsize=10, color='gray')
    
    ax.annotate('', xy=(2, 85.70), xytext=(1, 30.15),
                arrowprops=dict(arrowstyle='->', color='gray', lw=1.5))
    ax.text(1.5, 58, '+55.55%', ha='center', fontsize=10, color='gray')
    
    plt.tight_layout()
    
    # Save as PNG and SVG
    plt.savefig('paper/figures/three_way_baseline_comparison.png', dpi=300, bbox_inches='tight')
    plt.savefig('paper/figures/three_way_baseline_comparison.svg', bbox_inches='tight')
    print("Saved: three_way_baseline_comparison.png and .svg")
    plt.close()

def create_baseline_comparison():
    """Create the baseline comparison figure (two models)."""
    models = ['DictaBERT\n(General NER)', 'Our Fine-tuned\nJoint Model']
    f1_scores = [30.15, 85.70]
    colors = ['#ff7f0e', '#2ca02c']
    
    fig, ax = plt.subplots(figsize=(8, 6))
    
    bars = ax.bar(models, f1_scores, color=colors, edgecolor='black', linewidth=1.2, width=0.5)
    
    # Add value labels on bars
    for bar, score in zip(bars, f1_scores):
        height = bar.get_height()
        ax.annotate(f'{score:.2f}%',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha='center', va='bottom',
                    fontsize=14, fontweight='bold')
    
    ax.set_ylabel('Entity-level F1 Score (%)', fontsize=14)
    ax.set_title('Baseline Comparison: NER Performance on Hebrew Manuscript Dataset', fontsize=14, pad=15)
    ax.set_ylim(0, 100)
    
    # Add improvement annotation
    ax.annotate('', xy=(1, 85.70), xytext=(0, 30.15),
                arrowprops=dict(arrowstyle='->', color='gray', lw=2))
    ax.text(0.5, 58, '+55.55%', ha='center', fontsize=12, color='gray', fontweight='bold')
    
    plt.tight_layout()
    
    # Save as PNG and SVG
    plt.savefig('paper/figures/baseline_comparison.png', dpi=300, bbox_inches='tight')
    plt.savefig('paper/figures/baseline_comparison.svg', bbox_inches='tight')
    print("Saved: baseline_comparison.png and .svg")
    plt.close()

if __name__ == '__main__':
    print("Regenerating figures with updated values...")
    print("  - Our model NER F1: 85.70%")
    print("  - DictaBERT baseline: 30.15%")
    print("  - Improvement: +55.55%")
    print()
    
    create_three_way_comparison()
    create_baseline_comparison()
    
    print("\nDone! Figures have been regenerated.")


<?xml version="1.0" encoding="UTF-8"?>
<!--
// =====================================================
// Matrices
// =====================================================

MTEF stores matrix cells as a flat, ROW-MAJOR sequence of <slot>/<pile>
children, with <rows> and <cols> giving the shape.  Rebuilding the grid
therefore means chunking that sequence every `cols` cells.

History of the two bugs fixed here (found by feeding synthesized MTEF v5
binaries containing real MATRIX(5) records through the full pipeline —
see mtefx/_synthesize_ole.py):

  1. A catch-all `match="matrix" priority="10"` template emitted one <mtr>
     per cell, i.e. every matrix collapsed to an N x 1 column.  Because of
     its explicit priority it also out-ranked the three `matrix[h_just=...]`
     templates, which never fired.
  2. Those h_just templates were themselves wrong twice over: they chunked
     by `rows` instead of `cols`, and `h_just` is never emitted by the MTEF
     XML builder (`_build_matrix` writes options/valign/v_just/rows/cols
     only), so they could not have matched anyway.

The single template below chunks by `cols`, degrades safely to one cell per
row when `cols` is absent/1/NaN, and honours `h_just` if a future builder
starts emitting it.
-->

<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:xs="http://www.w3.org/2001/XMLSchema"
    exclude-result-prefixes="xs"
    version="1.0">

    <xsl:template match="matrix" priority="10">
        <xsl:variable name="ncols" select="number(cols)"/>
        <xsl:variable name="align">
            <xsl:choose>
                <xsl:when test="h_just='left'">left</xsl:when>
                <xsl:when test="h_just='right'">right</xsl:when>
                <xsl:otherwise>center</xsl:otherwise>
            </xsl:choose>
        </xsl:variable>
        <mtable columnalign="{$align}">
            <!-- Row starts: cells 1, cols+1, 2*cols+1, ...
                 `not($ncols > 1)` keeps the safe one-cell-per-row behaviour
                 when cols is 1, 0, missing or NaN. -->
            <xsl:apply-templates
                select="(slot | pile)[not($ncols &gt; 1) or position() mod $ncols = 1]"
                mode="matrix-row">
                <xsl:with-param name="ncols" select="$ncols"/>
                <xsl:with-param name="align" select="$align"/>
            </xsl:apply-templates>
        </mtable>
    </xsl:template>

    <xsl:template match="slot | pile" mode="matrix-row">
        <xsl:param name="ncols" select="1"/>
        <xsl:param name="align" select="'center'"/>
        <mtr>
            <xsl:apply-templates select="." mode="matrix-cell">
                <xsl:with-param name="align" select="$align"/>
            </xsl:apply-templates>
            <xsl:apply-templates
                select="(following-sibling::slot | following-sibling::pile)[position() &lt; $ncols]"
                mode="matrix-cell">
                <xsl:with-param name="align" select="$align"/>
            </xsl:apply-templates>
        </mtr>
    </xsl:template>

    <xsl:template match="slot" mode="matrix-cell">
        <xsl:param name="align" select="'center'"/>
        <mtd columnalign="{$align}">
            <xsl:apply-templates/>
        </mtd>
    </xsl:template>

    <xsl:template match="pile" mode="matrix-cell">
        <xsl:param name="align" select="'center'"/>
        <mtd columnalign="{$align}">
            <xsl:apply-templates select="."/>
        </mtd>
    </xsl:template>

</xsl:stylesheet>

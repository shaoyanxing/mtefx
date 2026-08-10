<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:xs="http://www.w3.org/2001/XMLSchema"
    exclude-result-prefixes="xs"
    version="1.0">

    <!-- Fences -->
    <!--
        所有 fence 字符必须带 stretchy="true"，否则 Word/WordPad 渲染时括号不随
        内容高度撑大、显示过小，且部分版本会判定 m:oMath 不可识别、弹出"无法识别
        的内容"对话框。MathML 默认 <mo> 是非 stretchy 的。
    -->
    <xsl:template match="tmpl[selector='tmPAREN']">
        <mrow>
            <mo stretchy="true">(</mo>
                <xsl:apply-templates select="slot[1] | pile[1]"/>
            <mo stretchy="true">)</mo>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmPAREN' and variation='tvFENCE_L' and not(variation='tvFENCE_R')]">
        <mrow><mo stretchy="true">(</mo> <xsl:apply-templates select="slot[1] | pile[1]"/></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmPAREN' and variation='tvFENCE_R' and not(variation='tvFENCE_L')]">
        <mrow> <xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">)</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmBRACK']">
        <mrow>
            <mo stretchy="true">[</mo>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
            <mo stretchy="true">]</mo>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmBRACE']">
        <mrow>
            <mo stretchy="true">{</mo>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
            <mo stretchy="true">}</mo>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmANGLE']">
        <mrow><mo stretchy="true">&#x2329;</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">&#x232A;</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmANGLE' and variation='tvFENCE_L' and not(variation='tvFENCE_R')]">
        <mrow>
            <mo stretchy="true">&#x2329;</mo>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmANGLE' and variation='tvFENCE_R' and not(variation='tvFENCE_L')]">
        <mrow>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
            <mo stretchy="true">&#x232A;</mo>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmBAR']">
        <mrow><mo stretchy="true">|</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">|</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmBAR' and variation='tvFENCE_L' and not(variation='tvFENCE_R')]">
        <mrow>
            <mo stretchy="true">|</mo>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmBAR' and variation='tvFENCE_R' and not(variation='tvFENCE_L')]">
        <mrow>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
            <mo stretchy="true">|</mo>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmFLOOR']">
        <mrow><mo stretchy="true">&#x230A;</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">&#x230B;</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmCEILING']">
        <mrow><mo stretchy="true">&#x2308;</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">&#x2309;</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmINTERVAL' and variation='tvINTV_LBLB']">
        <mrow> <mo stretchy="true">[</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">[</mo> </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmINTERVAL' and variation='tvINTV_RBRB']">
        <mrow><mo stretchy="true">]</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">]</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmINTERVAL' and variation='tvINTV_RBLB']">
        <mrow><mo stretchy="true">]</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">[</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmINTERVAL' and variation='tvINTV_LBRP']">
        <mrow><mo stretchy="true">[</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">)</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmINTERVAL' and variation='tvINTV_LPRB']">
        <mrow><mo stretchy="true">(</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">]</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmBRACK' and variation='tvFENCE_L' and not(variation='tvFENCE_R')]">
        <mrow> <mo stretchy="true">[</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmBRACK' and variation='tvFENCE_R' and not(variation='tvFENCE_L')]">
        <mrow><xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">]</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmBRACE' and variation='tvFENCE_L' and not(variation='tvFENCE_R')]">
        <mrow><mo stretchy="true">{</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmBRACE' and variation='tvFENCE_R' and not(variation='tvFENCE_L')]">
        <mrow><xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">}</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmDIRAC']">
        <mrow>
            <mo stretchy="true">&#x2329;</mo>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
            <mo stretchy="true">|</mo>
            <xsl:apply-templates select="slot[2] | pile[2]"/>
            <mo stretchy="true">&#x232A;</mo>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmDIRAC' and variation='tvDI_RIGHT' and not(variation='tvDI_LEFT')]">
        <mrow>
            <mo stretchy="true">|</mo>
            <xsl:apply-templates select="slot[2] | pile[2]"/>
            <mo stretchy="true">&#x232A;</mo>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmDIRAC' and variation='tvDI_LEFT' and not(variation='tvDI_RIGHT')]">
        <mrow>
            <mo stretchy="true">&#x2329;</mo>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
            <mo stretchy="true">|</mo>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmDBAR']">
        <mrow><mo stretchy="true">&#x2016;</mo> <xsl:apply-templates select="slot[1] | pile[1]"/> <mo stretchy="true">&#x2016;</mo></mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmDBAR' and variation='tvFENCE_L' and not(variation='tvFENCE_R')]">
        <mrow>
            <mo stretchy="true">&#x2016;</mo>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmDBAR' and variation='tvFENCE_R' and not(variation='tvFENCE_L')]">
        <mrow>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
            <mo stretchy="true">&#x2016;</mo>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmOBRACK']">
        <mrow>
            <mo stretchy="true">&#x301A;</mo>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
            <mo stretchy="true">&#x301B;</mo>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmOBRACK' and variation='tvFENCE_L' and not(variation='tvFENCE_R')]">
        <mrow>
            <mo stretchy="true">&#x301A;</mo>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmOBRACK' and variation='tvFENCE_R' and not(variation='tvFENCE_L')]">
        <mrow>
            <xsl:apply-templates select="slot[1] | pile[1]"/>
            <mo stretchy="true">&#x301B;</mo>
        </mrow>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmHBRACE']">
        <munder>
            <munder>
                <xsl:apply-templates select="slot[1] | pile[1]"/>
                <mo stretchy="true">&#xFE38;</mo>
            </munder>
            <xsl:apply-templates select="slot[2] | pile[2]"/>
        </munder>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmHBRACE' and variation='tvHB_TOP']">
        <mover>
            <mover>
                <xsl:apply-templates select="slot[1] | pile[1]"/>
                <mo stretchy="true">&#xFE37;</mo>
            </mover>
            <xsl:apply-templates select="slot[2] | pile[2]"/>
        </mover>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmHBRACK']">
        <munder>
            <munder>
                <xsl:apply-templates select="slot[1] | pile[1]"/>
                <mo stretchy="true">&#x23B5;</mo>
            </munder>
            <xsl:apply-templates select="slot[2] | pile[2]"/>
        </munder>
    </xsl:template>

    <xsl:template match="tmpl[selector='tmHBRACK' and variation='tvHB_TOP']">
        <mover>
            <mover>
                <xsl:apply-templates select="slot[1] | pile[1]"/>
                <mo stretchy="true">&#x23B4;</mo>
            </mover>
            <xsl:apply-templates select="slot[2] | pile[2]"/>
        </mover>
    </xsl:template>



</xsl:stylesheet>

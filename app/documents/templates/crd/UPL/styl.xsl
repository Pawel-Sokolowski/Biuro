<?xml version="1.0" encoding="UTF-8"?><xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:adr="http://crd.gov.pl/xml/schematy/adres/2009/11/09/" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" xmlns:inst="http://crd.gov.pl/xml/schematy/instytucja/2009/11/16/" xmlns:meta="http://crd.gov.pl/xml/schematy/meta/2009/11/16/" xmlns:oso="http://crd.gov.pl/xml/schematy/osoba/2009/11/16/" xmlns:str="http://crd.gov.pl/xml/schematy/struktura/2009/11/16/" xmlns:wnio="http://crd.gov.pl/wzor/2018/11/02/6046/" version="1.0">
	<xsl:template match="/">
		<xsl:for-each select="wnio:Dokument">
			<html xmlns="http://www.w3.org/1999/xhtml">
				<head/>
				<body>
					<p>
						<br/> </p>
					<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
						<tbody>
							<tr>
								<td colspan="2" style="border-width: 1pt; border-style: solid solid none; border-color: #000000; background-color: #ffffff; padding-left: 5px; width: 40%;">
									<strong>
										<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">1.  <span>
												<label>
													<span/>
												</label>
												<xsl:if test="true()">
													<span>
														<xsl:if test="wnio:TrescDokumentu/wnio:Wartosc/wnio:TypIdentyfikatoraPodatnika = 1">Identyfikator podatkowy NIP</xsl:if>
														<xsl:if test="wnio:TrescDokumentu/wnio:Wartosc/wnio:TypIdentyfikatoraPodatnika = 2">Numer PESEL </xsl:if>
													</span>
												</xsl:if>
											</span>  podatnika, płatnika lub inkasenta</span>
									</strong>
									<br/>
									<xsl:for-each select="wnio:TrescDokumentu/wnio:Wartosc/wnio:IdentyfikatorPESEL">
										<xsl:if test="/wnio:Dokument/wnio:TrescDokumentu/wnio:Wartosc/wnio:TypIdentyfikatoraPodatnika!=&#34;1&#34;">
											<table border="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td align="center">
															<p>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<strong>
																		<span>
																			<label>
																				<span/>
																			</label>
																			<xsl:if test="true()">
																				<span type="text">
																					<xsl:value-of select="wnio:PESEL"/>
																				</span>
																			</xsl:if>
																		</span>
																	</strong>
																</span>
															</p>
														</td>
													</tr>
												</tbody>
											</table>
										</xsl:if>
									</xsl:for-each>
									<xsl:for-each select="wnio:TrescDokumentu/wnio:Wartosc/wnio:IdentyfikatorNIP">
										<xsl:if test="/wnio:Dokument/wnio:TrescDokumentu/wnio:Wartosc/wnio:TypIdentyfikatoraPodatnika=&#34;1&#34;">
											<table border="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td align="center">
															<p style="text-align: center;">
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<strong>
																		<span>
																			<label>
																				<span/>
																			</label>
																			<xsl:if test="true()">
																				<span type="text">
																					<xsl:value-of select="wnio:NIP"/>
																				</span>
																			</xsl:if>
																		</span>
																	</strong>
																</span>
															</p>
														</td>
													</tr>
												</tbody>
											</table>
										</xsl:if>
									</xsl:for-each>
								</td>
								<td> </td>
							</tr>
							<tr>
								<td colspan="2" style="border-width: 1pt; border-style: solid; border-color: #000000; background-color: #ffffff; padding-left: 5px;">
									<strong> <span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">2. Numer identyfikacyjny podmiotu zagranicznego</span>
									</strong>
									<p style="text-align: center;">
										<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
											<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
												<strong>EU616 <span>
														<label>
															<span/>
														</label>
														<xsl:if test="true()">
															<span type="text">
																<xsl:value-of select="wnio:TrescDokumentu/wnio:Wartosc/wnio:PodmiotZagraniczny"/>
															</span>
														</xsl:if>
													</span>
												</strong>
											</span>
											<br/>
										</span>
									</p>
								</td>
								<td> </td>
							</tr>
							<tr>
								<td style="text-align: left; padding-left: 30px;">
									<strong>
										<span style="font-family: arial,helvetica,sans-serif; font-size: medium;">UPL-1</span>
									</strong>
								</td>
								<td> </td>
								<td> </td>
							</tr>
							<tr>
								<td colspan="3" style="text-align: center;">
									<span style="font-family: arial,helvetica,sans-serif; font-size: medium;">
										<strong>PEŁNOMOCNICTWO</strong>
									</span>
									<span style="font-family: arial,helvetica,sans-serif; font-size: medium;">
										<strong>DO PODPISYWANIA DEKLARACJI SKŁADANEJ</strong>
									</span>
									<span style="font-family: arial,helvetica,sans-serif; font-size: medium;">
										<strong>ZA POMOCĄ<br/> ŚRODKÓW KOMUNIKACJI ELEKTRONICZNEJ<br/>
										</strong>
									</span>
								</td>
							</tr>
							<tr>
								<td colspan="3" style="text-align: center;">
									<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
										<tbody>
											<tr>
												<td style="width: 40%;">
													<span style="font-family: arial,helvetica,sans-serif; font-size: small;"> </span>
												</td>
												<td style="width: 20%; text-align: left;"> </td>
												<td style="width: 40%;">
													<span style="font-family: arial,helvetica,sans-serif; font-size: small;"> </span>
												</td>
											</tr>
											<tr>
												<td style="width: 40%;">
													<span style="font-family: arial,helvetica,sans-serif; font-size: small;"> </span>
												</td>
												<td align="center" style="width: 20%;"> </td>
												<td style="width: 40%; border-width: 1pt; border-style: solid solid none; border-color: #000000; background-color: #ffffff; padding-left: 5px;">
													<strong>
														<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">3. Kolejny nr egz. / ogółem liczba egzemplarzy</span>
													</strong>
													<br/>
													<p align="center">
														<span style="font-family: arial,helvetica,sans-serif;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																<span>
																	<label>
																		<span/>
																	</label>
																	<xsl:if test="true()">
																		<span type="text">
																			<xsl:value-of select="wnio:TrescDokumentu/wnio:Wartosc/wnio:NumerEgzemplarza"/>
																		</span>
																	</xsl:if>
																</span>
															</span> / <span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																<span>
																	<label>
																		<span/>
																	</label>
																	<xsl:if test="true()">
																		<span type="text">
																			<xsl:value-of select="wnio:TrescDokumentu/wnio:Wartosc/wnio:LiczbaEgzemplarzy"/>
																		</span>
																	</xsl:if>
																</span>
															</span>
															<br/>
														</span>
													</p>
												</td>
											</tr>
										</tbody>
									</table>
								</td>
							</tr>
							<tr>
								<td style="border-width: 1pt; border-style: solid none none solid; border-color: #000000; background-color: #d3d3d3; padding-left: 5px; width: 20%;" valign="top">
									<span style="font-family: arial,helvetica,sans-serif; font-size: small;">Podstawa prawna:</span>
								</td>
								<td colspan="2" style="border-width: 1pt; border-style: solid solid none none; border-color: #000000; background-color: #d3d3d3; padding-left: 5px; width: 80%;" valign="top">
									<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
										<span>
											<label>
												<span/>
											</label>
											<xsl:if test="true()">
												<span>
													<xsl:value-of select="wnio:TrescDokumentu/wnio:Wartosc/wnio:PodstawaPrawna"/>
												</span>
											</xsl:if>
										</span>
									</span>
								</td>
							</tr>
							<tr>
								<td style="border-width: 1pt; border-style: none none none solid; border-color: #000000; background-color: #d3d3d3; padding-left: 5px; width: 20%;" valign="top">
									<span style="font-family: arial,helvetica,sans-serif; font-size: small;">Składający:</span>
								</td>
								<td colspan="2" style="border-width: 1pt; border-style: none solid none none; background-color: #d3d3d3; padding-left: 5px; width: 80%; border-color: #000000;">
									<span style="font-family: arial,helvetica,sans-serif; font-size: small;">Podatnik, płatnik, inkasent lub podmiot zagraniczny udzielający pełnomocnictwa do podpisywania deklaracji składanej za pomocą środków komunikacji elektronicznej.</span>
								</td>
							</tr>
							<tr>
								<td style="border-width: 1pt; border-style: none none none solid; border-color: #000000; background-color: #d3d3d3; padding-left: 5px; width: 20%;" valign="top">
									<span style="font-family: arial,helvetica,sans-serif; font-size: small; background-color: #d3d3d3;">Miejsce składania:</span>
								</td>
								<td colspan="2" style="border-width: 1pt; border-style: none solid none none; border-color: #000000; background-color: #d3d3d3; padding-left: 5px; width: 80%;">
									<span style="font-family: arial,helvetica,sans-serif; font-size: small;">Naczelnik urzędu skarbowego właściwy w sprawach ewidencji podatników i płatników albo Naczelnik Drugiego Urzędu Skarbowego Warszawa-Śródmieście właściwy w sprawach dotyczących podmiotu zagranicznego, a jeżeli pełnomocnictwo jest składane w formie dokumentu elektronicznego Szef Krajowej Administracji Skarbowej.</span>
								</td>
							</tr>
						</tbody>
					</table>
					<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
						<tbody>
							<tr>
								<td colspan="2" style="border-width: 1pt; border-style: solid solid none solid; border-color: #000000; background-color: #d3d3d3; padding-left: 5px;">
									<strong>
										<span style="font-family: arial,helvetica,sans-serif; font-size: small;">A. ORGAN PODATKOWY, DO KTÓREGO JEST ADRESOWANE  PEŁNOMOCNICTWO<br/>
										</span>
									</strong>
								</td>
							</tr>
							<tr>
								<td style="width: 5%; border-width: 1pt; border-style: none solid; border-color: #000000; background-color: #d3d3d3;"> </td>
								<td style="width: 95%; border-width: 1pt; border-style: solid solid solid none; border-color: #000000; padding-left: 5px;">
									<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
										<strong>4. <span>
												<label>
													<span/>
												</label>
												<xsl:if test="true()">
													<span>
														<xsl:choose>
															<xsl:when test="contains(concat(concat(' ', normalize-space(wnio:TrescDokumentu/wnio:Wartosc/wnio:Naczelnik4)),' '),  ' 1 ' )">
																<span>☒</span>
															</xsl:when>
															<xsl:otherwise>
																<span>☐</span>
															</xsl:otherwise>
														</xsl:choose>
														<label>
															<span>  </span>
														</label>
													</span>
												</xsl:if>
											</span>Naczelnik urzędu skarbowego właściwy w sprawach ewidencji podatników i płatników </strong>
										<br/>
									</span>
									<xsl:for-each select="wnio:TrescDokumentu/wnio:Wartosc/wnio:AdresyUrzedow">
										<xsl:if test="../wnio:Naczelnik4=&#34;1&#34;">
											<table border="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td align="center">
															<span>
																<label>
																	<span/>
																</label>
																<xsl:if test="wnio:DaneUrzedu!='-- wybierz --'">
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																		<xsl:value-of select="wnio:DaneUrzedu"/>
																	</span>
																</xsl:if>
															</span>
															<span>
																<label>
																	<span/>
																</label>
																<xsl:if test="true()">
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																		<xsl:value-of select="wnio:NazwaUrzedu"/>
																	</span>
																</xsl:if>
															</span>
														</td>
													</tr>
												</tbody>
											</table>
										</xsl:if>
									</xsl:for-each>
								</td>
							</tr>
							<tr>
								<td style="width: 5%; border-width: 1pt; border-style: none solid solid; border-color: #000000; background-color: #d3d3d3;"> </td>
								<td style="width: 95%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
									<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
										<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
											<strong>5. <span>
													<label>
														<span/>
													</label>
													<xsl:if test="true()">
														<span>
															<xsl:choose>
																<xsl:when test="contains(concat(concat(' ', normalize-space(wnio:TrescDokumentu/wnio:Wartosc/wnio:Naczelnik5)),' '),  ' 1 ' )">
																	<span>☒</span>
																</xsl:when>
																<xsl:otherwise>
																	<span>☐</span>
																</xsl:otherwise>
															</xsl:choose>
															<label>
																<span>  </span>
															</label>
														</span>
													</xsl:if>
												</span>Naczelnik Drugiego Urzędu Skarbowego Warszawa-Śródmieście </strong>
											<br/>
										</span>
									</span>
								</td>
							</tr>
						</tbody>
					</table>
					<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
						<tbody>
							<tr>
								<td style="border-width: 1pt; border-style: solid none none solid; border-color: #000000; background-color: #d3d3d3; padding-left: 5px;" valign="top">
									<strong>
										<span style="font-family: arial,helvetica,sans-serif; font-size: small;">B.<br/>
										</span>
									</strong>
								</td>
								<td style="border-width: 1pt; border-style: solid solid none none; border-color: #000000; background-color: #d3d3d3; padding-left: 5px;">
									<strong>
										<span style="font-family: arial,helvetica,sans-serif; font-size: small;">DANE PODATNIKA, PŁATNIKA, INKASENTA</span>
									</strong>
									<strong>
										<span style="font-family: arial,helvetica,sans-serif; font-size: small;">LUB PODMIOTU ZAGRANICZNEGO,</span>
									</strong>
									<strong>
										<span style="font-family: arial,helvetica,sans-serif; font-size: small;">KTÓRY USTANAWIA PEŁNOMOCNIKA</span>
									</strong>
								</td>
							</tr>
							<tr>
								<td colspan="2" style="border-width: 1pt; border-style: solid solid none; border-color: #000000; background-color: #d3d3d3; padding-left: 10px;" valign="top">
									<span style="font-family: arial,helvetica,sans-serif; font-size: small;">B.1. DANE IDENTYFIKACYJNE<br/>                * - dotyczy podmiotu niebędącego osobą fizyczną                        ** - dotyczy podmiotu będącego osobą fizyczną</span>
								</td>
							</tr>
						</tbody>
					</table>
					<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
						<tbody>
							<tr>
								<td style="width: 5%; border-width: 1pt; border-style: none solid none solid; border-color: #000000; background-color: #d3d3d3;"> </td>
								<td style="width: 95%; border-width: 1pt; border-style: solid solid none none; border-color: #000000; padding-left: 5px;">
									<strong>
										<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">6. Rodzaj podmiotu:</span>
									</strong>
								</td>
							</tr>
							<tr>
								<td style="width: 5%; border-width: 1pt; border-style: none solid; border-color: #000000; background-color: #d3d3d3;"> </td>
								<td align="center" style="width: 95%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px; text-align: center;" valign="top">
									<p>
										<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
											<span>
												<label>
													<span/>
												</label>
												<xsl:if test="true()">
													<span>
														<xsl:choose>
															<xsl:when test="normalize-space(wnio:TrescDokumentu/wnio:Wartosc/wnio:PodmiotZobowiazany) = normalize-space(' 1 ')">
																<span>☒</span>
															</xsl:when>
															<xsl:otherwise>
																<span>☐</span>
															</xsl:otherwise>
														</xsl:choose>
														<label>
															<span>1. podmiot niebędący osobą fizyczną </span>
														</label>
													</span>
													<span>
														<xsl:choose>
															<xsl:when test="normalize-space(wnio:TrescDokumentu/wnio:Wartosc/wnio:PodmiotZobowiazany) = normalize-space(' 2 ')">
																<span>☒</span>
															</xsl:when>
															<xsl:otherwise>
																<span>☐</span>
															</xsl:otherwise>
														</xsl:choose>
														<label>
															<span>2. osoba fizyczna </span>
														</label>
													</span>
												</xsl:if>
											</span>
										</span>
									</p>
								</td>
							</tr>
							<tr>
								<td style="width: 5%; border-width: 1pt; border-style: none solid; border-color: #000000; background-color: #d3d3d3;"> </td>
								<td align="center" style="width: 95%; border-width: 1pt; border-style: none solid none none; border-color: #000000; padding-left: 5px; text-align: left;">
									<strong>
									<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">7. Nazwa pełna * / Nazwisko, pierwsze imię **</span>
									</strong>
									<br/>
									<xsl:for-each select="wnio:DaneDokumentu/str:Nadawcy/meta:Podmiot/inst:Instytucja">
										<xsl:if test="/wnio:Dokument/wnio:TrescDokumentu/wnio:Wartosc/wnio:PodmiotZobowiazany=&#34;1&#34;"> <span style="font-family: arial,helvetica,sans-serif;">
												<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
													<span>
														<label>
															<span/>
														</label>
														<xsl:if test="true()">
															<span type="text">
																<xsl:value-of select="inst:NazwaInstytucji"/>
															</span>
														</xsl:if>
													</span>
												</span>
											</span>
										</xsl:if>
									</xsl:for-each>
									<xsl:for-each select="wnio:DaneDokumentu/str:Nadawcy/meta:Podmiot/oso:Osoba">
										<xsl:if test="/wnio:Dokument/wnio:TrescDokumentu/wnio:Wartosc/wnio:PodmiotZobowiazany!=&#34;1&#34;"> <span style="font-family: arial,helvetica,sans-serif;">
												<span style="font-size: small;">
													<span>
														<label>
															<span/>
														</label>
														<xsl:if test="true()">
															<span type="text">
																<xsl:value-of select="oso:Nazwisko"/>
															</span>
														</xsl:if>
													</span>
												</span>
											</span>
											<span style="font-family: arial,helvetica,sans-serif; font-size: small;">  <span>
													<label>
														<span/>
													</label>
													<xsl:if test="true()">
														<span type="text">
															<xsl:value-of select="oso:Imie"/>
														</span>
													</xsl:if>
												</span>
											</span>
										</xsl:if>
									</xsl:for-each>
								</td>
							</tr>
						</tbody>
					</table>
					<xsl:for-each select="wnio:DaneDokumentu/str:Nadawcy/meta:Podmiot/oso:Osoba/adr:Adres">
						<xsl:if test="/wnio:Dokument/wnio:TrescDokumentu/wnio:Wartosc/wnio:PodmiotZobowiazany!=&#34;1&#34;">
							<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
								<tbody>
									<tr>
										<td colspan="2" style="border-width: 1pt; border-style: solid solid none; border-color: #000000; background-color: #d3d3d3; padding-left: 10px;">
											<span style="font-family: arial,helvetica,sans-serif; font-size: small;">B.2. ADRES SIEDZIBY * / AKTUALNY ADRES ZAMIESZKANIA **<br/>
												<span style="font-family: arial,helvetica,sans-serif; font-size: small;">       Podmiot zagraniczny podaje obowiązkowo kraj i miejscowość.</span>
											</span>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: none solid; border-color: #000000; background-color: #d3d3d3;"> </td>
										<td style="width: 95%; border-width: 1pt; border-style: solid solid none none; border-color: #000000;">
											<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td style="width: 30%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>8. Kraj</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Uwagi"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
														</td>
														<td colspan="2" style="width: 35%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>9. Województwo</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Wojewodztwo"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
														</td>
														<td colspan="3" style="width: 35%; border-width: 1pt; border-style: none none solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>10. Powiat</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Powiat"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
														</td>
													</tr>
													<tr>
														<td style="width: 30%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>11. Gmina</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Gmina"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
														</td>
														<td colspan="2" style="width: 35%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>12. Ulica</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Ulica"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
														</td>
														<td style="width: 20%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>13. Nr domu</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Budynek"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
														</td>
														<td style="width: 15%; border-width: 1pt; border-style: none none solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>14. Nr lokalu</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Lokal"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
														</td>
													</tr>
													<tr>
														<td colspan="2" style="width: 45%; border-width: 1pt; border-style: none solid none none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>15. Miejscowość</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Miejscowosc"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
														</td>
														<td style="width: 20%; border-width: 1pt; border-style: none solid none none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>16. Kod pocztowy</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:KodPocztowy"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
														</td>
														<td colspan="3" style="width: 35%; border-width: 1pt; border-style: none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>17. Poczta</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Poczta"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
								</tbody>
							</table>
						</xsl:if>
					</xsl:for-each>
					<xsl:for-each select="wnio:DaneDokumentu/str:Nadawcy/meta:Podmiot/inst:Instytucja/adr:Adres">
						<xsl:if test="/wnio:Dokument/wnio:TrescDokumentu/wnio:Wartosc/wnio:PodmiotZobowiazany=&#34;1&#34;">
							<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
								<tbody>
									<tr>
										<td colspan="2" style="border-width: 1pt; border-style: solid solid none; border-color: #000000; background-color: #d3d3d3; padding-left: 10px;">
											<span style="font-family: arial,helvetica,sans-serif; font-size: small;">B.2. ADRES SIEDZIBY * / AKTUALNY ADRES ZAMIESZKANIA **<br/>       Podmiot zagraniczny podaje obowiązkowo kraj i miejscowość.</span>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: none solid solid solid; border-color: #000000; background-color: #d3d3d3;"> </td>
										<td style="width: 95%; border-width: 1pt; border-style: solid solid solid none; border-color: #000000;">
											<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td style="width: 30%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>8. Kraj</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Uwagi"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td colspan="2" style="width: 35%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>9. Województwo</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Wojewodztwo"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td colspan="3" style="width: 35%; border-width: 1pt; border-style: none none solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>10. Powiat</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Powiat"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
													</tr>
													<tr>
														<td style="width: 30%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>11. Gmina</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Gmina"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td colspan="2" style="width: 35%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>12. Ulica</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Ulica"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td style="width: 20%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>13. Nr domu</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Budynek"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td style="width: 15%; border-width: 1pt; border-style: none none solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>14. Nr lokalu</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Lokal"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
													</tr>
													<tr>
														<td colspan="2" style="width: 45%; border-width: 1pt; border-style: none solid none none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>15. Miejscowość</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Miejscowosc"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td style="width: 20%; border-width: 1pt; border-style: none solid none none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>16. Kod pocztowy</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:KodPocztowy"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td colspan="3" style="width: 35%; border-width: 1pt; border-style: none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>17. Poczta</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Poczta"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
								</tbody>
							</table>
						</xsl:if>
					</xsl:for-each>
					<xsl:for-each select="wnio:DaneDokumentu/str:Nadawcy/meta:Podmiot/oso:Osoba/adr:Kontakt">
						<xsl:if test="/wnio:Dokument/wnio:TrescDokumentu/wnio:Wartosc/wnio:PodmiotZobowiazany!=&#34;1&#34;">
							<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
								<tbody>
									<tr>
										<td colspan="2" style="border-width: 1pt; border-style: none solid none; border-color: #000000; background-color: #d3d3d3; padding-left: 10px;">
											<span style="font-family: arial,helvetica,sans-serif; font-size: small;">B.3. KONTAKT </span>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: none solid; border-color: #000000; background-color: #d3d3d3;"> </td>
										<td style="width: 95%; border-width: 1pt; border-style: solid solid none none; border-color: #000000; padding-left: 5px;">
											<table border="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td style="border-width: 1pt; border-style: none solid none none; border-color: #000000; width: 30%;"><span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>18. Telefon</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Telefon"/>
																			</span>
																		</xsl:if>
																	</span>
																	<br/>
																</span>
															</span>
														</td>
														<td style="border-width: 1pt; border-style: none solid none none; border-color: #000000; width: 30%; padding-left: 5px;"><span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>19. Faks</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small; padding-left: 5px;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Faks"/>
																			</span>
																		</xsl:if>
																	</span>
																	<br/>
																</span>
															</span>
														</td>
														<td colspan="2" style="border-width: 1pt; border-style: none; border-color: #000000; width: 40%; padding-left: 5px;"><span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>20. E-mail</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small; padding-left: 5px;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Email"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<br/>
															</span>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
								</tbody>
							</table>
						</xsl:if>
					</xsl:for-each>
					<xsl:for-each select="wnio:DaneDokumentu/str:Nadawcy/meta:Podmiot/inst:Instytucja/adr:Kontakt">
						<xsl:if test="/wnio:Dokument/wnio:TrescDokumentu/wnio:Wartosc/wnio:PodmiotZobowiazany=&#34;1&#34;">
							<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
								<tbody>
									<tr>
										<td colspan="2" style="border-width: 1pt; border-style: none solid solid solid; border-color: #000000; background-color: #d3d3d3; padding-left: 10px;">
											<span style="font-family: arial,helvetica,sans-serif; font-size: small;">B.3. KONTAKT</span>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: none solid; border-color: #000000; background-color: #d3d3d3;"> </td>
										<td style="width: 95%; border-width: 1pt; border-style:none solid none none; border-color: #000000; padding-left: 5px;">
											<table border="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td style="border-width: 1pt; border-style: none solid none none; border-color: #000000; width: 30%;"> <span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>18. Telefon</strong>
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small; padding-left: 5px;">
																		<span>
																			<label>
																				<span/>
																			</label>
																			<xsl:if test="true()">
																				<span type="text">
																					<xsl:value-of select="adr:Telefon"/>
																				</span>
																			</xsl:if>
																		</span>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td style="border-width: 1pt; border-style: none solid none none; border-color: #000000; width: 30%;"> <span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>19. Faks</strong>
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small; padding-left: 5px;">
																		<span>
																			<label>
																				<span/>
																			</label>
																			<xsl:if test="true()">
																				<span type="text">
																					<xsl:value-of select="adr:Faks"/>
																				</span>
																			</xsl:if>
																		</span>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td colspan="2" style="border-width: 1pt; border-style: none; border-color: #000000; width: 40%;"> <span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>20. E-mail</strong>
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small; padding-left: 5px;">
																		<span>
																			<label>
																				<span/>
																			</label>
																			<xsl:if test="true()">
																				<span type="text">
																					<xsl:value-of select="adr:Email"/>
																				</span>
																			</xsl:if>
																		</span>
																	</span>
																</span>
															</span>
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<br/>
															</span>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
								</tbody>
							</table>
						</xsl:if>
					</xsl:for-each>
					<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
						<tbody>
							<tr>
								<td colspan="2" style="border-width: 1pt; border-style: solid solid none; border-color: #000000; background-color: #d3d3d3; padding-left: 5px; width: 5%;" valign="top">
									<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
										<strong>C. </strong>
										<strong>DANE PEŁNOMOCNIKA UPOWAŻNIONEGO DO PODPISYWANIA DEKLARACJI SKŁADANEJ ZA POMOCĄ</strong><br/>
										<strong>    ŚRODKÓW KOMUNIKACJI ELEKTRONICZNEJ</strong>
										<br/>
									</span>
								</td>
							</tr>
						</tbody>
					</table>
					<xsl:for-each select="wnio:TrescDokumentu/wnio:Wartosc/wnio:Pelnomocnik">
						<xsl:if test="true()">
							<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
								<tbody>
									<tr>
										<td colspan="2" style="border-width: 1pt; border-style: solid solid none solid; border-color: #000000; background-color: #d3d3d3; padding-left: 10px;">
											<span style="font-family: arial,helvetica,sans-serif; font-size: small;">C.1. DANE IDENTYFIKACYJNE</span>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: none solid none solid; border-color: #000000; background-color: #d3d3d3;"> </td>
										<td style="width: 95%; border-width: 1pt; border-style: none solid none none; border-color: #000000;">
											<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td align="left" colspan="2" style="width: 50%; border-width: 1pt; border-style: solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<strong>
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span>
																				<xsl:if test="wnio:Identyfikator = 1">21. Identyfikator podatkowy NIP</xsl:if>
																				<xsl:if test="wnio:Identyfikator = 2">21. Numer PESEL</xsl:if>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</strong>
															<br/>
															<xsl:for-each select="wnio:IdentyfikatorNIP">
																<xsl:if test="../wnio:Identyfikator=&#34;1&#34;">
																	<table border="0" cellspacing="0" style="width: 100%;">
																		<tbody>
																			<tr>
																				<td align="center">
																					<p>
																						<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																							<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																								<span>
																									<label>
																										<span/>
																									</label>
																									<xsl:if test="true()">
																										<span type="text">
																											<xsl:value-of select="oso:NIP"/>
																										</span>
																									</xsl:if>
																								</span>
																							</span>
																						</span>
																					</p>
																				</td>
																			</tr>
																		</tbody>
																	</table>
																</xsl:if>
															</xsl:for-each>
															<xsl:for-each select="wnio:IdentyfikatorPESEL">
																<xsl:if test="../wnio:Identyfikator!=&#34;1&#34;">
																	<table border="0" cellspacing="0" style="width: 100%;">
																		<tbody>
																			<tr>
																				<td align="center">
																					<p>
																						<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																							<span>
																								<label>
																									<span/>
																								</label>
																								<xsl:if test="true()">
																									<span type="text">
																										<xsl:value-of select="oso:PESEL"/>
																									</span>
																								</xsl:if>
																							</span>
																						</span>
																					</p>
																				</td>
																			</tr>
																		</tbody>
																	</table>
																</xsl:if>
															</xsl:for-each>
														</td>
													</tr>
													<tr>
														<td align="left" style="width: 50%; border-width: 1pt; border-style: solid solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																	<strong>22. Nazwisko</strong>
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																		<span>
																			<label>
																				<span/>
																			</label>
																			<xsl:if test="true()">
																				<span type="text">
																					<xsl:value-of select="oso:Nazwisko"/>
																				</span>
																			</xsl:if>
																		</span>
																	</span>
																	<br/>
																</span>
															</span>
														</td>
														<td style="width: 50%; border-width: 1pt; border-style: solid none none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																	<strong>23. Pierwsze imię</strong>
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																		<span>
																			<label>
																				<span/>
																			</label>
																			<xsl:if test="true()">
																				<span type="text">
																					<xsl:value-of select="oso:Imie"/>
																				</span>
																			</xsl:if>
																		</span>
																	</span>
																	<br/>
																</span>
															</span>
														</td>
													</tr>
													<tr>
														<td align="left" colspan="2" style="width: 50%; border-width: 1pt; border-style: solid none none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<strong>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">24. Data urodzenia (dzień - miesiąc - rok)</span>
															</strong>
															<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small; text-align: center;">
																			<xsl:if test="true()">
																				<xsl:value-of select="wnio:DataUrodzenia"/>
																			</xsl:if>
																		<br/>
																</span>
														</td>
													</tr>
													<tr>
														<td align="left" colspan="2" style="width: 50%; border-width: 1pt; border-style: solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<strong>
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">25. Numer i seria paszportu lub innego dokumentu potwierdzającego tożsamość lub inny numer identyfikacyjny</span>
															</strong>
															<br style="font-family: arial,helvetica,sans-serif; font-size: x-small;"/>
															<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																<span>
																	<label>
																		<span/>
																	</label>
																	<xsl:if test="true()">
																		<span type="text">
																			<xsl:value-of select="wnio:NumerDokumentu"/>
																		</span>
																	</xsl:if>
																</span>
															</span>
														</td>
													</tr>
													<tr>
														<td align="left" style="width: 50%; border-width: 1pt; border-style: solid solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>26. Kraj wydania dokumentu </strong>
																<br/>
																<span style="ffont-family: arial,helvetica,sans-serif; font-size: small;">
																				<xsl:value-of select="wnio:KrajWydania"/>
																</span>
																<br/>
															</span>
														</td>
														<td style="width: 50%; border-width: 1pt; border-style: solid none none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>27. Adres elektroniczny</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:URIKontakt"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
								</tbody>
							</table>
						</xsl:if>
					</xsl:for-each>
					<xsl:for-each select="wnio:TrescDokumentu/wnio:Wartosc/wnio:Pelnomocnik/adr:Adres">
						<xsl:if test="true()">
							<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
								<tbody>
									<tr>
										<td colspan="2" style="border-width: 1pt; border-style: solid solid none solid; border-color: #000000; background-color: #d3d3d3; padding-left: 10px;">
											<span style="font-family: arial,helvetica,sans-serif; font-size: small;">C.2. ADRES DO DORĘCZEŃ<br style="font-family: arial,helvetica,sans-serif; font-size: small;"/>
											</span>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: none solid none solid; border-color: #000000; background-color: #d3d3d3;"> </td>
										<td style="width: 95%; border-width: 1pt; border-style: none solid none none; border-color: #000000;">
											<table border="0" cellpadding="2" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td style="width: 30%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>28. Kraj</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Uwagi"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td colspan="2" style="width: 35%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>29. Województwo</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Wojewodztwo"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td colspan="3" style="width: 35%; border-width: 1pt; border-style: none none solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>30. Powiat</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Powiat"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
													</tr>
													<tr>
														<td style="width: 30%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>31. Gmina</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Gmina"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td colspan="2" style="width: 35%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">3<strong>2. Ulica</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Ulica"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td style="width: 20%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>33. Nr domu</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Budynek"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td style="width: 15%; border-width: 1pt; border-style: none none solid none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>34. Nr lokalu</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Lokal"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
													</tr>
													<tr>
														<td colspan="2" style="width: 45%; border-width: 1pt; border-style: none solid none none; border-color: #000000; padding-left: 5px;">
															<span style="font- family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>
																	<span style="font-family: arial,helvetica,sans-serif;">35. Miejscowość</span>
																</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Miejscowosc"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td style="width: 20%; border-width: 1pt; border-style: none solid none none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>36. Kod pocztowy</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:KodPocztowy"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
														<td colspan="3" style="width: 35%; border-width: 1pt; border-style: none; border-color: #000000; padding-left: 5px;">
															<span style="font- family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>
																	<span style="font-family: arial,helvetica,sans-serif;">37. Poczta</span>
																</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Poczta"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
																<br/>
															</span>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
								</tbody>
							</table>
						</xsl:if>
					</xsl:for-each>
					<xsl:for-each select="wnio:TrescDokumentu/wnio:Wartosc/wnio:Pelnomocnik/adr:Kontakt">
						<xsl:if test="true()">
							<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
								<tbody>
									<tr>
										<td colspan="2" style="border-width: 1pt; border-style: solid solid none solid; border-color: #000000; background-color: #d3d3d3; padding-left: 10px;">
											<span style="font-family: arial,helvetica,sans-serif; font-size: small;">C.3. KONTAKT </span>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: none solid solid; border-color: #000000; background-color: #d3d3d3;"> </td>
										<td style="width: 95%; border-width: 1pt; border-style: none solid solid none; border-color: #000000;">
											<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td align="left" style="width: 30%; border-width: 1pt; border-style: solid solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																<span style="font-size: x-small;">
																	<strong>38. Telefon</strong>
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																		<span>
																			<label>
																				<span/>
																			</label>
																			<xsl:if test="true()">
																				<span type="text">
																					<xsl:value-of select="adr:Telefon"/>
																				</span>
																			</xsl:if>
																		</span>
																	</span>
																</span>
															</span>
														</td>
														<td align="left" style="width: 30%; border-width: 1pt; border-style: solid solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>39. Faks</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																	<span>
																		<label>
																			<span/>
																		</label>
																		<xsl:if test="true()">
																			<span type="text">
																				<xsl:value-of select="adr:Faks"/>
																			</span>
																		</xsl:if>
																	</span>
																</span>
															</span>
														</td>
														<td style="width: 40%; border-width: 1pt; border-style: solid none none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																<strong>
																	<span style="font-size: x-small;">40. E-mail</span>
																</strong>
																<br/>
															</span>
															<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																<span>
																	<label>
																		<span/>
																	</label>
																	<xsl:if test="true()">
																		<span type="text">
																			<xsl:value-of select="adr:Email"/>
																		</span>
																	</xsl:if>
																</span>
															</span>
															<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																<br/>
															</span>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
								</tbody>
							</table>
						</xsl:if>
					</xsl:for-each>
					<xsl:for-each select="wnio:TrescDokumentu/wnio:Wartosc/wnio:OkresObowiazywania">
						<xsl:if test="true()">
							<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
								<tbody>
									<tr>
										<td colspan="2" style="border-width: 1pt; border-style: solid solid none; border-color: #000000; background-color: #d3d3d3; padding-left: 5px;">
											<strong>
												<span style="font-family: arial,helvetica,sans-serif; font-size: small;">D. OKRES OBOWIĄZYWANIA PEŁNOMOCNICTWA<br/>
												</span>
											</strong>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: none solid none solid; border-color: #000000; background-color: #d3d3d3;"> </td>
										<td style="width: 95%; border-width: 1pt; border-style: none solid none none; border-color: #000000;">
											<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td align="left" colspan="2" style="width: 50%; border-width: 1pt; border-style: solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<table border="0" cellspacing="0" style="width: 100%;">
																<tbody>
																	<tr>
																		<td style="border-width: 1pt; border-style: none solid none none; border-color: #000000; padding-left: 5px; width: 50%;">
																			<strong>
																				<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																					<span style="text-align: left;">41.</span> Ważne od<span style="font-family: arial,helvetica,sans-serif;"> </span>
																				</span>
																				<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																					<xsl:if test="true()">
																						<xsl:value-of select="wnio:DataOd"/>
																					</xsl:if>
																				</span>
																			</strong>
																		</td>
																		<td style="padding-left: 5px;">
																			<strong>
																				<span style="text-align: left; font-family: arial,helvetica,sans-serif; font-size: x-small;">42. Ważne do </span>
																				<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																					<xsl:if test="true()">
																						<xsl:value-of select="wnio:Waznosc/wnio:DataDo"/>
																					</xsl:if>
																				</span>
																			</strong>
																		</td>
																	</tr>
																</tbody>
															</table>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
								</tbody>
							</table>
						</xsl:if>
					</xsl:for-each>
					<xsl:for-each select="wnio:TrescDokumentu/wnio:Wartosc/wnio:PodpisWnioskodawcy">
						<xsl:if test="true()">
							<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
								<tbody>
									<tr>
										<td colspan="2" style="border-width: 1pt; border-style: solid solid none; border-color: #000000; background-color: #d3d3d3; padding-left: 5px;" valign="top">
											<strong>
												<span style="font-family: arial,helvetica,sans-serif; font-size: small;">E. OŚWIADCZENIE PODATNIKA (PŁATNIKA, INKASENTA  LUB PODMIOTU ZAGRANICZNEGO)</span>
											</strong>
										</td>
									</tr>
								</tbody>
							</table>
							<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
								<tbody>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: none solid; border-color: #000000; background-color: #d3d3d3; text-align: center;"> </td>
										<td style="width: 95%; border-width: 1pt; border-style: solid solid none none; border-color: #000000;">
											<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td style="width: 50%; border-width: 1pt; border-style: none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<p style="text-align: center;">
																<span style="font-family: arial,helvetica,sans-serif; font-size: small; text-align: center;">Oświadczam, że osobę wymienioną w części C upoważniam do podpisywania deklaracji składanej za pomocą<br/> środków komunikacji elektronicznej</span>
															</p>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: solid solid none; border-color: #000000; background-color: #d3d3d3; text-align: center;"> <span style="font-family: arial,helvetica,sans-serif; font-size: small;">1</span>
										</td>
										<td style="width: 95%; border-width: 1pt; border-style: solid solid none none; border-color: #000000;">
											<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td align="left" style="width: 50%; border-width: 1pt; border-style: none solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>43. Nazwisko</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																				<xsl:value-of select="wnio:Nazwisko1"/>
																</span>
															</span>
														</td>
														<td style="width: 50%; border-width: 1pt; border-style: none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>44. Imię</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																				<xsl:value-of select="wnio:Imie1"/>
																</span>
																<br/>
															</span>
														</td>
													</tr>
													<tr>
														<td align="left" style="width: 50%; border-width: 1pt; border-style: none solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;"><strong>45. Stanowisko / Funkcja</strong>
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																					<xsl:value-of select="wnio:Funkcja1"/>
																	</span>
															</span>
														</td>
														<td style="width: 50%; border-width: 1pt; border-style: none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>46. Podpis</strong>
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																					<xsl:value-of select="wnio:Podpis1"/>
																	</span>
															</span>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: solid solid none; border-color: #000000; background-color: #d3d3d3; text-align: center;">
											<span style="font-family: arial,helvetica,sans-serif; font-size: small;">2</span>
										</td>
										<td style="width: 95%; border-width: 1pt; border-style: solid solid none none; border-color: #000000;">
											<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td align="left" style="width: 50%; border-width: 1pt; border-style: none solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>47. Nazwisko</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																				<xsl:value-of select="wnio:Nazwisko2"/>
																</span>
															</span>
														</td>
														<td style="width: 50%; border-width: 1pt; border-style: none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>48. Imię</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																				<xsl:value-of select="wnio:Imie2"/>
																</span>
																<br/>
															</span>
														</td>
													</tr>
													<tr>
														<td align="left" style="width: 50%; border-width: 1pt; border-style: none solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>49. Stanowisko / Funkcja</strong>
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																					<xsl:value-of select="wnio:Funkcja2"/>
																	</span>
															</span>
														</td>
														<td style="width: 50%; border-width: 1pt; border-style: none; border-color: #000000; padding-left: 5px;">
															<strong>
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">50</span>
															</strong>
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>. Podpis</strong>
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																					<xsl:value-of select="wnio:Podpis2"/>
																	</span>
															</span>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: solid solid none; border-color: #000000; background-color: #d3d3d3; text-align: center;">
											<span style="font-family: arial,helvetica,sans-serif; font-size: small;">3</span>
										</td>
										<td style="width: 95%; border-width: 1pt; border-style: solid solid none none; border-color: #000000;">
											<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td align="left" style="width: 50%; border-width: 1pt; border-style: none solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>51. Nazwisko</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																				<xsl:value-of select="wnio:Nazwisko3"/>
																</span>
															</span>
														</td>
														<td style="width: 50%; border-width: 1pt; border-style: none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>52. Imię</strong>
																<br/>
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																				<xsl:value-of select="wnio:Imie3"/>
																</span>
																<br/>
															</span>
														</td>
													</tr>
													<tr>
														<td align="left" style="width: 50%; border-width: 1pt; border-style: none solid none none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>53. Stanowisko / Funkcja</strong>
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																					<xsl:value-of select="wnio:Funkcja3"/>
																	</span>
															</span>
														</td>
														<td style="width: 50%; border-width: 1pt; border-style: none; border-color: #000000; padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>54. Podpis</strong>
																	<br/>
																	<span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																					<xsl:value-of select="wnio:Podpis3"/>
																	</span>
															</span>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
									<tr>
										<td style="width: 5%; border-width: 1pt; border-style: solid; border-color: #000000; background-color: #d3d3d3;"> </td>
										<td style="width: 95%; border-width: 1pt; border-style: solid solid solid none; border-color: #000000;">
											<table border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
												<tbody>
													<tr>
														<td colspan="2" style="border-width: 1pt; border-style: none none solid; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;">
															<strong>
																<span style="font-size: x-small;">
																	<span style="font-family: arial,helvetica,sans-serif;">55. Data (dzień - miesiąc - rok)</span>
																</span>
															</strong>
															<br/>
															<p style="text-align: center;">
																<span style="font-size: small;">
																	<span style="font-family: arial,helvetica,sans-serif;">
																		<xsl:if test="true()">
																			<xsl:value-of select="wnio:DataPodpisu"/>
																		</xsl:if>
																	</span>
																</span>
															</p>
														</td>
													</tr>
													<tr>
														<td style="width: 50%; border-width: 1pt; border-style: none solid solid none; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;" valign="top">
															<strong>
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">56. Uwierzytelnienie odpisu pełnomocnictwa przez</span>
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">pełnomocnika wskazanego w poz. 57</span>
															</strong>
														</td>
														<td style="width: 50%; border-width: 1pt; border-style: none none solid; border-color: #000000; background-color: #ffffff; padding-left: 5px; text-align: left;">
															<strong>
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">57. Podpis pełnomocnika będącego adwokatem, radcą prawnym lub doradcą podatkowym </span></strong><span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">(uwierzytelniającego udzielone mu pełnomocnictwo)</span>					
															<br/>
															<p style="text-align: center;">
																<span style="font-family: arial,helvetica,sans-serif; font-size: small;">Dokument podpisany elektronicznie</span>
															</p>
														</td>
													</tr>
													<tr>
														<td colspan="2" style="padding-left: 5px;">
															<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																<strong>58. Data uwierzytelnienia odpisu zawiadomienia </strong></span>
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">(dzień - miesiąc - rok) </span>
																<strong><span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">i miejsce jego sporządzenia</span></strong>
															<br/>
															<p style="text-align: center;">
																<span style="font-family: arial,helvetica,sans-serif; font-size: x-small;">
																	<span style="font-size: small;">
																		<xsl:if test="true()">
																			<xsl:value-of select="wnio:DataUwierzytelnienia"/>
																		</xsl:if>
																	</span>  <span style="font-family: arial,helvetica,sans-serif; font-size: small;">
																		<span>
																			<label>
																				<span/>
																			</label>
																			<xsl:if test="true()">
																				<span type="text">
																					<xsl:value-of select="adr:Miejscowosc"/>
																				</span>
																			</xsl:if>
																		</span>
																	</span>
																	<br/>
																</span>
															</p>
														</td>
													</tr>
												</tbody>
											</table>
										</td>
									</tr>
								</tbody>
							</table>
						</xsl:if>
					</xsl:for-each>
					<p style="text-align: justify;"> </p>
					<xsl:call-template name="Podpis_v23.99"/>
				</body>
			</html>
		</xsl:for-each>
	</xsl:template>
     <xsl:template name="Podpis_v23.99">
        <div align="right" style="text-align: right;font-size: 10px;font-family: Verdana;line-height: 20px;">
           <table style="float: right;">
              <tr>
                 <td style="width: 340px;">
                    <h5 class="acc" align="center">Podpisy elektroniczne</h5>
                    <p align="center" style="text-align: center;font-size: 10px;font-family: Verdana;line-height: 20px;">
                       <xsl:choose>
                          <xsl:when test="string-length(//wnio:Dokument/ds:Signature/ds:SignatureValue) &gt; 0">
                             <xsl:text>Dokument został podpisany, aby go zweryfikować należy użyć oprogramowania do weryfikacji podpisu</xsl:text>
                             <xsl:choose>
                                <xsl:when test="count(//wnio:Dokument/ds:Signature/ds:Object/descendant::*[local-name() = 'SigningTime']) &gt; 1">
                                <br/>
                                   <xsl:text>Daty złożenia podpisu: </xsl:text>
                                   <xsl:for-each select="//wnio:Dokument/ds:Signature/ds:Object/descendant::*[local-name() = 'SigningTime']">
                                   <xsl:value-of select="."/>
                                   </xsl:for-each>
                                </xsl:when>
                                <xsl:when test="count(//wnio:Dokument/ds:Signature/ds:Object/descendant::*[local-name() = 'SigningTime']) = 1">
                                <br/>
                                   <xsl:text>Data złożenia podpisu: </xsl:text>
                                   <xsl:for-each select="//wnio:Dokument/ds:Signature/ds:Object/descendant::*[local-name() = 'SigningTime']">
                                   <xsl:value-of select="."/>
                                   </xsl:for-each>
                                </xsl:when>
                                <xsl:when test="0 &gt;= count(//wnio:Dokument/ds:Signature/ds:Object/descendant::*[local-name() = 'SigningTime'])">
                                </xsl:when>
                             </xsl:choose>
                          </xsl:when>
                          <xsl:otherwise>
                             <xsl:text>Dokument nie zawiera podpisu</xsl:text>
                          </xsl:otherwise>
                       </xsl:choose>
                    <br/>
                       <span name="info_v99.23" style="font-size: 7pt;font-weight: bold;">
                       </span>
                    </p>
                 </td>
              </tr>
           </table>
        </div>
     <div name="clear_v23.23" style="clear: both;"/>
	</xsl:template>
</xsl:stylesheet>
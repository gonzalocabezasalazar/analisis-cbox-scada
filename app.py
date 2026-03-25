import streamlit as st
import pandas as pd
import numpy as np
import io

# 1. CONFIGURACIÓN DE PÁGINA
st.set_page_config(page_title="Detección de Fusibles - SCADA Solar", layout="wide", page_icon="☀️")

# --- BARRA LATERAL: GEMELO DIGITAL DE LA PLANTA ---
st.sidebar.header("⚙️ Configuración de Planta")
plant_name = st.sidebar.text_input("Nombre de la Planta", "Planta Roble")
potencia_mw = st.sidebar.number_input("Potencia Total (MW)", min_value=1.0, value=9.0)

st.sidebar.markdown("---")
st.sidebar.subheader("🔌 Datos del Panel y Strings")
string_default = st.sidebar.number_input("Strings por CBox (Estándar)", min_value=1, value=26)
paneles_por_string = st.sidebar.number_input("Paneles por String", min_value=1, value=30)
panel_imp = st.sidebar.number_input("Corriente Imp del Panel (A)", min_value=1.0, value=9.5)

st.sidebar.markdown("---")
st.sidebar.subheader("⚠️ Cajas con Configuración Especial")
st.sidebar.caption("Ingresa las excepciones. Formato: Inversor-CBox:Strings")
# Dejamos precargada tu configuración de Roble
excepciones_input = st.sidebar.text_area("Excepciones", "1-04:18\n2-07:17\n3-04:17")

# Procesamos las excepciones dinámicamente en un diccionario
cajas_especiales = {}
if excepciones_input:
    for linea in excepciones_input.split('\n'):
        if ':' in linea:
            cbox, strings = linea.split(':')
            cajas_especiales[cbox.strip()] = int(strings.strip())

# --- FUNCIONES MATEMÁTICAS CORE ---

def tara_sensores(df):
    """
    Detecta el horario nocturno, calcula el error de offset de cada CBox
    y limpia los datos del día restando esa "corriente fantasma".
    """
    # 1. Aseguramos que la columna de fecha sea datetime (asumimos que es la primera columna)
    col_tiempo = df.columns[0]
    df[col_tiempo] = pd.to_datetime(df[col_tiempo], errors='coerce', dayfirst=True)
    
    # 2. Identificamos todas las columnas que son Combiner Boxes (Empiezan con 'String')
    cbox_cols = [col for col in df.columns if 'String' in col]
    
    # Asegurar que sean numéricas
    for col in cbox_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
    # 3. Detectar la "Noche" o momentos apagados. 
    # Sumamos la corriente de toda la planta. Si es casi cero (< 1% del máximo histórico), es noche.
    df['Corriente_Total'] = df[cbox_cols].sum(axis=1)
    umbral_noche = df['Corriente_Total'].max() * 0.01 
    mascara_noche = df['Corriente_Total'] < umbral_noche
    
    # 4. Calcular el Offset promedio por caja durante la noche
    offsets = df.loc[mascara_noche, cbox_cols].mean().fillna(0)
    
    # 5. Limpieza: Restar el offset a toda la columna y asegurar que no haya amperajes negativos
    df_limpio = df.copy()
    for col in cbox_cols:
        df_limpio[col] = df_limpio[col] - offsets[col]
        # Cualquier valor negativo que resulte de la resta lo forzamos a 0
        df_limpio[col] = df_limpio[col].clip(lower=0) 
        
    return df_limpio, offsets, cbox_cols

# --- UI PRINCIPAL ---
st.title("⚡ Analizador Estadístico de Fusibles (Multiplanta)")
st.markdown(f"**Planta Activa:** {plant_name} | **Análisis de 3 días para descartar falsos positivos.**")

# DRAG & DROP EFÍMERO
archivos_subidos = st.file_uploader("Arrastra aquí tus 3 archivos Excel/CSV", type=["csv", "xlsx"], accept_multiple_files=True)

if archivos_subidos:
    if len(archivos_subidos) > 3:
        st.warning("Has subido más de 3 archivos. Solo analizaremos los primeros 3 para mantener la consistencia estadística.")
        archivos_subidos = archivos_subidos[:3]
        
    st.success(f"✅ {len(archivos_subidos)} archivos cargados en memoria efímera con éxito. Listos para extraer la verdad.")
    
    # Aquí irá el código para unificar los archivos y hacer la magia espacial
    # ...
    # Continuación de app.py ... (debajo del success de carga)
    
    st.markdown("---")
    st.subheader("🔍 Procesando Análisis Longitudinal...")

    # Lista para guardar los datos limpios de los 3 días
    datos_limpios = []
    cbox_nombres = [] # Guardaremos los nombres de las columnas para detectar inversores
    
    with st.spinner("Analizando y calibrando sensores en memoria..."):
        for i, archivo in enumerate(archivos_subidos):
            # Leemos el archivo (CSV o Excel)
            if archivo.name.endswith('.csv'):
                df_dia = pd.read_csv(archivo)
            else:
                df_dia = pd.read_excel(archivo)
                
            # Limpiamos y calibramos la noche
            df_calibrado, offsets, cols = tara_sensores(df_dia)
            cbox_nombres = cols # Actualizamos la lista de nombres
            
            # Buscamos el PICO MÁXIMO DEL DÍA (filtrando la noche)
            # Solo analizamos cuando la planta genera más del 30% de su capacidad para evitar ruido
            df_dia_generando = df_calibrado[df_calibrado['Corriente_Total'] > (df_calibrado['Corriente_Total'].max() * 0.3)]
            
            # Tomamos el percentil 95 (P95) de cada caja como su "Corriente Pico Consistente"
            # (No usamos el máximo absoluto para evitar picos falsos o fallos de lectura de 1 segundo)
            pico_dia = df_dia_generando[cbox_nombres].quantile(0.95).to_dict()
            
            # Guardamos los picos de este día
            pico_dia['Dia'] = f"Día {i+1}"
            datos_limpios.append(pico_dia)

    # Convertimos los picos de los 3 días en un DataFrame ordenado
    df_analisis = pd.DataFrame(datos_limpios).set_index('Dia').T
    
    # === LA MAGIA DINÁMICA: DETECCIÓN DE INVERSORES Y CONFIGURACIÓN ===
    
    resultados = []
    
    # Extraemos los Inversores únicos (ej: de "String 1-04" saca el "1")
    inversores_detectados = list(set([col.split(' ')[1].split('-')[0] for col in cbox_nombres]))
    inversores_detectados.sort()
    
    # Analizamos caja por caja
    for cbox in cbox_nombres:
        # Extraer nomenclatura (ej: "1-04")
        nomenclatura = cbox.split(' ')[1] 
        inversor_actual = nomenclatura.split('-')[0]
        
        # ¿Cuántos strings tiene esta caja? (Busca en excepciones, si no, usa el estándar)
        hilos_reales = cajas_especiales.get(nomenclatura, string_default)
        
        # Calculamos el promedio de la corriente pico de esta caja en los 3 días
        corriente_promedio_3dias = df_analisis.loc[cbox].mean()
        
        # Normalizamos: ¿Cuántos Amperios aporta un solo string en esta caja? (I_unit)
        corriente_por_string = corriente_promedio_3dias / hilos_reales
        
        resultados.append({
            'ID Caja': nomenclatura,
            'Inversor': inversor_actual,
            'Strings Configurados': hilos_reales,
            'Corriente Pico (Prom. 3 Días)': round(corriente_promedio_3dias, 1),
            'Corriente Normalizada (I_unit)': round(corriente_por_string, 2)
        })
        
    df_resultados = pd.DataFrame(resultados)
    
    # === EL ANÁLISIS ESPACIAL: PROMEDIO DE CONTIGUOS ===
    
    # Vamos a comparar cada caja SOLO con las de su mismo inversor
    diagnostico_final = []
    
    for inversor in inversores_detectados:
        # Filtramos solo las cajas de este inversor
        cajas_inv = df_resultados[df_resultados['Inversor'] == inversor].copy()
        
        # El "Promedio Sano Local" (P90 de la corriente normalizada para descartar las rotas)
        # Esto nos dice cuánto debería dar un string sano en esa zona específica de la planta
        promedio_local_i_unit = cajas_inv['Corriente Normalizada (I_unit)'].quantile(0.90)
        
        for index, row in cajas_inv.iterrows():
            cbox_id = row['ID Caja']
            hilos = row['Strings Configurados']
            corriente_real = row['Corriente Pico (Prom. 3 Días)']
            
            # ¿Cuánto DEBERÍA generar esta caja según sus vecinas?
            corriente_esperada = promedio_local_i_unit * hilos
            diferencia = corriente_real - corriente_esperada
            
            # Estimación de fusibles operados
            # Dividimos la diferencia total por lo que aporta un string sano.
            strings_perdidos = round(abs(diferencia) / promedio_local_i_unit, 1)
            
            # Lógica de Alertas (Diagnóstico)
            estado = "✅ Saludable"
            if strings_perdidos >= 1.8:
                estado = f"🚨 Crítico: Faltan ~{round(strings_perdidos)} strings"
            elif strings_perdidos >= 0.8:
                estado = f"🔴 Alerta: Falta ~1 string"
            elif strings_perdidos >= 0.4:
                estado = f"🟡 Precaución: Desviación severa (suciedad o sombra)"
                
            diagnostico_final.append({
                'ID Caja': cbox_id,
                'Inversor': inversor,
                'Strings': hilos,
                'Corriente Real (A)': corriente_real,
                'Corriente Esperada (A)': round(corriente_esperada, 1),
                'Diferencia (A)': round(diferencia, 1),
                'Diagnóstico': estado
            })

    # Convertimos a DataFrame final y ordenamos
    df_final = pd.DataFrame(diagnostico_final)
    
    # === MOSTRAR EL DATO DURO ===
    st.subheader("📋 Orden de Trabajo (Diagnóstico Consolidado)")
    
    # Filtramos para mostrar solo los problemas (para que el técnico no vea 100 cajas sanas)
    df_problemas = df_final[df_final['Diagnóstico'] != "✅ Saludable"].sort_values(by='Diferencia (A)')
    
    if df_problemas.empty:
        st.success("¡Excelente! No se detectaron anomalías consistentes en la planta durante estos 3 días.")
    else:
        st.error(f"Se encontraron anomalías en {len(df_problemas)} cajas agrupadas.")
        st.dataframe(df_problemas, use_container_width=True)
        
# --- REEMPLAZA ESTE BLOQUE ---
        # csv = df_problemas.to_csv(index=False).encode('utf-8')
        # st.download_button(
        #     label="Descargar Orden de Trabajo (CSV)",
        #     data=csv,
        #     file_name='orden_trabajo_fusibles.csv',
        #     mime='text/csv',
        # )

        # --- POR ESTE NUEVO BLOQUE DE EXCEL ---
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df_problemas.to_excel(writer, index=False, sheet_name='Fallas_Detectadas')
        
        st.download_button(
            label="📥 Descargar Orden de Trabajo (Excel)",
            data=buffer.getvalue(),
            file_name='orden_trabajo_fusibles.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        
        import plotly.express as px

    # === VISUALIZACIÓN: MAPA DE CALOR (HEATMAP) ===
    st.markdown("---")
    st.subheader("🗺️ Mapa de Calor Evolutivo")
    st.caption("Visualiza la consistencia de la falla a lo largo de los días. Los valores representan la desviación porcentual (%) respecto al rendimiento esperado local. Rojos intensos indican posibles fusibles operados.")

    # 1. Filtro interactivo por Inversor
    inversor_seleccionado = st.selectbox("⚡ Selecciona un Inversor para visualizar:", inversores_detectados)

    if inversor_seleccionado:
        # Preparamos la matriz de datos exclusiva para el Heatmap
        cajas_del_inversor = [c for c in cbox_nombres if c.split(' ')[1].split('-')[0] == inversor_seleccionado]
        df_heatmap_raw = df_analisis.loc[cajas_del_inversor].copy()
        
        # DataFrame vacío para guardar los porcentajes
        df_heatmap_pct = pd.DataFrame(index=df_heatmap_raw.index, columns=df_heatmap_raw.columns)
        
        # Calculamos la desviación diaria (para ver si la falla es de 1 día o de los 3)
        for dia in df_heatmap_raw.columns:
            i_units_dia = []
            
            # Normalizamos todas las cajas del inversor en ese día
            for cbox in df_heatmap_raw.index:
                nomenclatura = cbox.split(' ')[1]
                hilos = cajas_especiales.get(nomenclatura, string_default)
                i_units_dia.append(df_heatmap_raw.loc[cbox, dia] / hilos)
            
            # Sacamos la Referencia Local del día (P90)
            p90_dia = pd.Series(i_units_dia).quantile(0.90)
            
            # Calculamos la desviación en % de cada caja vs su expectativa
            for cbox in df_heatmap_raw.index:
                nomenclatura = cbox.split(' ')[1]
                hilos = cajas_especiales.get(nomenclatura, string_default)
                corriente_real = df_heatmap_raw.loc[cbox, dia]
                corriente_esperada = p90_dia * hilos
                
                if corriente_esperada > 0:
                    desviacion = ((corriente_real - corriente_esperada) / corriente_esperada) * 100
                else:
                    desviacion = 0
                    
                df_heatmap_pct.loc[cbox, dia] = round(desviacion, 1)
        
        # Limpiamos los nombres del Eje Y para que sean más legibles (CBox 1-01 en vez de String 1-01)
        df_heatmap_pct.index = [c.replace('String', 'CBox') for c in df_heatmap_pct.index]
        
        # 2. Renderizado del Gráfico con Plotly
        # Usamos la escala RdYlGn (Red-Yellow-Green). Invertida implícitamente por el orden de los datos.
        fig = px.imshow(
            df_heatmap_pct,
            text_auto=True,
            aspect="auto",
            color_continuous_scale="RdYlGn", # Rojo (Bajo rendimiento) a Verde (Rendimiento Óptimo)
            zmin=-10,  # Saturamos el rojo si pierde más del 10% (Falla grave)
            zmax=2,    # Saturamos el verde si genera igual o un 2% más que el promedio
            labels=dict(x="Día Analizado", y="Combiner Box", color="Desviación (%)"),
            title=f"Mapa de Desviación Energética - Inversor {inversor_seleccionado}"
        )
        
        # Ajustamos el tamaño para que sea fácil de leer
        fig.update_layout(height=600)
        st.plotly_chart(fig, use_container_width=True)